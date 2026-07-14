import enum
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from oasis.config import load_config
from oasis.index.db import open_db
from oasis.index.embeddings import SentenceTransformerEmbedder
from oasis.index.keyword import MATCH_END, MATCH_START, KeywordIndex
from oasis.index.pipeline import index_directory
from oasis.index.vector import VectorIndex
from oasis.llm.manager import ensure_ollama
from oasis.query.parser import ParsedQuery, parse_query
from oasis.query.reranker import CrossEncoderReranker
from oasis.query.retriever import DEFAULT_TOP_N, hybrid_search
from oasis.query.snippets import text_snippet


class SearchMode(str, enum.Enum):
    keyword = "keyword"
    semantic = "semantic"
    hybrid = "hybrid"


class _ChunksPerSecColumn(ProgressColumn):
    def render(self, task) -> Text:  # type: ignore[override]
        speed = task.speed
        if speed is None:
            return Text("—", style="progress.data.speed")
        return Text(f"{speed:.0f} chunks/s", style="progress.data.speed")


app = typer.Typer(
    name="oasis",
    help="Oasis — natural-language file search.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
_console = Console()
_err = Console(stderr=True)

# Persists the file paths from the most recent search so `oasis open N` works.
_LAST_RESULTS_PATH: Path = Path.home() / ".oasis" / "last_results.json"


def _save_last_results(paths: list[Path]) -> None:
    try:
        _LAST_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_RESULTS_PATH.write_text(json.dumps([str(p) for p in paths]))
    except OSError:
        pass  # Never let a cache write break the search output


def _db_path(override: Path | None) -> Path:
    return override or load_config().db_path


def _human_size(n: int) -> str:
    size: float = n
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _highlight_snippet(raw: str) -> Text:
    """Convert \x02/\x03 FTS sentinels to bold-yellow Rich Text."""
    out = Text()
    parts = raw.split(MATCH_START)
    out.append(parts[0])
    for part in parts[1:]:
        if MATCH_END in part:
            match, rest = part.split(MATCH_END, 1)
            out.append(match, style="bold yellow")
            out.append(rest)
        else:
            out.append(part)
    return out


def _render_results_table(rows: list[tuple[Path, str | None, Text]]) -> None:
    cwd = Path.cwd()
    table = Table(box=None, show_header=True, header_style="bold dim", padding=(0, 2))
    table.add_column("#", style="bold", justify="right", no_wrap=True, min_width=2)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Snippet")
    for i, (path, title, snippet) in enumerate(rows, 1):
        try:
            display = path.relative_to(cwd)
        except ValueError:
            display = path
        file_link = Text(str(display), style=f"cyan link {path.resolve().as_uri()}")
        table.add_row(str(i), file_link, title or "", snippet)
    _console.print(table)


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

_STATUS_STYLE: dict[str, str] = {
    "indexed": "green",
    "skipped": "dim",
    "failed": "bold red",
    "unsupported": "dim",
}


_EMBED_PROGRESS_COLUMNS = (
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    MofNCompleteColumn(),
    TextColumn("•"),
    TimeRemainingColumn(),
    TextColumn("•"),
    _ChunksPerSecColumn(),
)


@app.command()
def index(
    path: Path = typer.Argument(..., help="Directory to index"),
    db: Path | None = typer.Option(None, "--db", help="SQLite database path"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-index all files, ignoring change detection"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print each file as it is processed"),
) -> None:
    """Walk PATH and index every supported file into the search database."""
    if not path.is_dir():
        _err.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(1)

    db_path = _db_path(db)
    conn = open_db(db_path)

    with _console.status("[dim]Initializing embedding model…[/dim]", spinner="dots"):
        emb = SentenceTransformerEmbedder()
        vec_idx = VectorIndex(db_path.with_name(db_path.stem + ".lance"), dimension=emb.dimension)

    if verbose:
        def on_file(p: Path, status: str) -> None:
            style = _STATUS_STYLE.get(status, "")
            _console.print(Text.assemble((f"{status:<11}", style), f"  {p}"))

        embed_p: list[Progress] = []
        embed_t: list[int] = []

        def on_chunks_progress(done: int, total: int) -> None:
            if not embed_p:
                p = Progress(*_EMBED_PROGRESS_COLUMNS, console=_console)
                p.start()
                embed_p.append(p)
                embed_t.append(p.add_task("[cyan]Embedding[/cyan]…", total=total))
            embed_p[0].update(embed_t[0], completed=done)
            if done >= total:
                embed_p[0].stop()

        stats = index_directory(
            conn, path, force=force, on_file=on_file,
            vector_index=vec_idx, embedder=emb,
            on_chunks_progress=on_chunks_progress,
        )
    else:
        with Progress(*_EMBED_PROGRESS_COLUMNS, console=_console) as progress:
            scan_task = progress.add_task("Scanning…", total=None)
            embed_task_id: list[int] = []

            def on_file(p: Path, status: str) -> None:
                progress.update(scan_task, description=f"[dim]{status}[/dim]  [cyan]{p.name}[/cyan]")

            def on_chunks_progress(done: int, total: int) -> None:
                if not embed_task_id:
                    progress.update(scan_task, visible=False)
                    embed_task_id.append(
                        progress.add_task("[cyan]Embedding[/cyan]…", total=total)
                    )
                progress.update(embed_task_id[0], completed=done)

            stats = index_directory(
                conn, path, force=force, on_file=on_file,
                vector_index=vec_idx, embedder=emb,
                on_chunks_progress=on_chunks_progress,
            )

    conn.close()

    parts: list[str] = [f"[green]{stats['indexed']} indexed[/green]"]
    if stats["skipped"]:
        parts.append(f"[dim]{stats['skipped']} skipped[/dim]")
    if stats["unsupported"]:
        parts.append(f"[dim]{stats['unsupported']} unsupported[/dim]")
    if stats["failed"]:
        parts.append(f"[red]{stats['failed']} failed[/red]")
    if stats["permission_denied"]:
        parts.append(f"[yellow]{stats['permission_denied']} permission denied[/yellow]")

    _console.print("Done — " + "  ".join(parts))
    if stats["permission_denied"] and not stats["indexed"]:
        _console.print(
            "[yellow]Nothing could be read.[/yellow] On macOS, grant Full Disk Access to your "
            "terminal in System Settings › Privacy & Security."
        )
    _console.print(f"[dim]db: {db_path}[/dim]")


# ---------------------------------------------------------------------------
# search helpers
# ---------------------------------------------------------------------------


def _footer(n: int, mode: str, db_path: Path, *, llm_parsed: bool = False) -> str:
    parts = [f"{n} result(s)", f"mode: {mode}"]
    if llm_parsed:
        parts.append("parsed")
    parts.append(f"db: {db_path}")
    return "\n[dim]" + "  ·  ".join(parts) + "[/dim]"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@app.command(name="search")
def cmd_search(
    query: str = typer.Argument(..., help="Search query"),
    db: Path | None = typer.Option(None, "--db", help="SQLite database path"),
    limit: int = typer.Option(DEFAULT_TOP_N, "--limit", "-n", help="Maximum number of results"),
    mode: SearchMode = typer.Option(
        SearchMode.hybrid,
        "--mode", "-m",
        case_sensitive=False,
        help="Retrieval strategy: keyword (FTS5), semantic (vector), or hybrid (fused + reranked).",
    ),
    raw: bool = typer.Option(
        False, "--raw",
        help="Skip NL parsing and search using the raw query string directly.",
    ),
) -> None:
    """Search the index and print matching files with highlighted snippets."""
    db_path = _db_path(db)

    if not db_path.exists():
        _err.print("[red]No index found.[/red] Run [bold]oasis index <path>[/bold] first.")
        raise typer.Exit(1)

    lance_path = db_path.with_name(db_path.stem + ".lance")

    # ---------------------------------------------------- NL query parsing
    # parsed is always a valid ParsedQuery — either from the LLM or a minimal
    # fallback so that downstream code never needs to handle None.
    parsed: ParsedQuery
    llm_parsed = False
    if raw:
        parsed = ParsedQuery(semantic_query=query)
    else:
        with _console.status("[dim]Parsing query…[/dim]", spinner="dots"):
            llm = ensure_ollama()
            if llm is not None:
                try:
                    parsed = parse_query(query, llm)
                    llm_parsed = True
                except Exception:
                    parsed = ParsedQuery(semantic_query=query)
            else:
                parsed = ParsedQuery(semantic_query=query)

    from oasis.query.retriever import _build_vec_where  # local import avoids circular at module level

    # ------------------------------------------------------------------ keyword
    if mode == SearchMode.keyword:
        from oasis.query.retriever import _build_fts_query, _build_kw_filters
        conn = open_db(db_path)
        try:
            kw_results = KeywordIndex(conn).search(
                _build_fts_query(parsed), limit=limit, **_build_kw_filters(parsed)
            )
        except sqlite3.OperationalError as exc:
            _err.print(f"[red]Query error:[/red] {exc}")
            _err.print('Tip: wrap phrases in double quotes — e.g. oasis search \\"machine learning\\"')
            raise typer.Exit(1)
        finally:
            conn.close()

        if not kw_results:
            _console.print("[dim]No results.[/dim]")
            return

        rows = [(r.path, r.title, _highlight_snippet(r.snippet)) for r in kw_results]
        _render_results_table(rows)
        _console.print(_footer(len(rows), "keyword", db_path, llm_parsed=llm_parsed))
        _save_last_results([r.path for r in kw_results])
        return

    # ------------------------------------------------- semantic / hybrid: load models
    with _console.status("[dim]Loading models…[/dim]", spinner="dots"):
        emb = SentenceTransformerEmbedder()
        vec_idx = VectorIndex(lance_path, dimension=emb.dimension)

    # ----------------------------------------------------------------- semantic
    if mode == SearchMode.semantic:
        vec_where = _build_vec_where(parsed)
        query_vec = emb.embed([parsed.semantic_query])[0]
        # Over-fetch to leave room for per-doc chunk dedup.
        raw_results = vec_idx.search(query_vec, limit=limit * 3, where=vec_where)

        best: dict[str, object] = {}
        for r in raw_results:
            if r.path not in best or r.score < best[r.path].score:  # type: ignore[union-attr]
                best[r.path] = r
        deduped = sorted(best.values(), key=lambda r: r.score)[:limit]  # type: ignore[attr-defined]

        if not deduped:
            _console.print("[dim]No results.[/dim]")
            return

        rows = [
            (Path(r.path), None, _highlight_snippet(text_snippet(r.text, parsed.semantic_query)))  # type: ignore[attr-defined]
            for r in deduped
        ]
        _render_results_table(rows)
        _console.print(_footer(len(rows), "semantic", db_path, llm_parsed=llm_parsed))
        _save_last_results([Path(r.path) for r in deduped])  # type: ignore[attr-defined]
        return

    # ------------------------------------------------------------------ hybrid
    conn = open_db(db_path)
    try:
        # Fetch extra candidates so the reranker has a larger pool to work with.
        candidates = hybrid_search(conn, vec_idx, emb, parsed, top_n=max(limit * 2, 20))
    except sqlite3.OperationalError as exc:
        _err.print(f"[red]Query error:[/red] {exc}")
        _err.print('Tip: wrap phrases in double quotes — e.g. oasis search \\"machine learning\\"')
        raise typer.Exit(1)
    finally:
        conn.close()

    reranker = CrossEncoderReranker()
    final = reranker.rerank(parsed.semantic_query, candidates, top_n=limit)

    if not final:
        _console.print("[dim]No results.[/dim]")
        return

    rows = [(r.path, r.title, _highlight_snippet(r.snippet)) for r in final]
    _render_results_table(rows)
    _console.print(_footer(len(final), "hybrid", db_path, llm_parsed=llm_parsed))
    _save_last_results([r.path for r in final])


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    db: Path | None = typer.Option(None, "--db", help="SQLite database path"),
) -> None:
    """Show index statistics: document count, database size, last indexed time."""
    db_path = _db_path(db)

    if not db_path.exists():
        _console.print(f"[dim]No index at {db_path}.[/dim]")
        _console.print("Run [bold]oasis index <path>[/bold] to create one.")
        return

    conn = open_db(db_path)
    idx = KeywordIndex(conn)
    count = idx.count()
    last_at = idx.last_indexed_at()
    conn.close()

    last_str = (
        datetime.fromtimestamp(last_at).strftime("%Y-%m-%d %H:%M:%S")
        if last_at is not None
        else "—"
    )

    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column("key", style="dim")
    table.add_column("value")
    table.add_row("Documents", f"[bold]{count:,}[/bold]")
    table.add_row("DB size", _human_size(db_path.stat().st_size))
    table.add_row("Last indexed", last_str)
    table.add_row("DB path", str(db_path))

    _console.print(table)


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

@app.command()
def reset(
    db: Path | None = typer.Option(None, "--db", help="SQLite database path"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete the index database (with confirmation)."""
    db_path = _db_path(db)

    if not db_path.exists():
        _console.print(f"[dim]Nothing to reset — no index at {db_path}.[/dim]")
        return

    if not yes:
        typer.confirm(f"Delete index at {db_path}?", abort=True)

    db_path.unlink()
    for companion in (Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
        if companion.exists():
            companion.unlink()

    _console.print(f"[green]Deleted:[/green] {db_path}")


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------

@app.command(name="open")
def cmd_open(
    n: int = typer.Argument(..., help="Result number from the last search"),
) -> None:
    """Open a search result in the system default application."""
    if not _LAST_RESULTS_PATH.exists():
        _err.print("[red]No recent search.[/red] Run [bold]oasis search <query>[/bold] first.")
        raise typer.Exit(1)

    try:
        paths = json.loads(_LAST_RESULTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        _err.print("[red]Could not read last search results — try searching again.[/red]")
        raise typer.Exit(1)

    if not 1 <= n <= len(paths):
        _err.print(f"[red]No result #{n}.[/red] Last search returned {len(paths)} result(s).")
        raise typer.Exit(1)

    path = Path(paths[n - 1])

    if not path.exists():
        _err.print(f"[red]File no longer exists:[/red] {path}")
        raise typer.Exit(1)

    subprocess.run(["open", str(path)], check=False)
    _console.print(f"[dim]Opening {path.name}[/dim]")
