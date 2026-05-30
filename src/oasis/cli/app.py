import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from oasis.config import load_config
from oasis.index.db import open_db
from oasis.index.keyword import MATCH_END, MATCH_START, KeywordIndex
from oasis.index.pipeline import index_directory

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


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

_STATUS_STYLE: dict[str, str] = {
    "indexed": "green",
    "skipped": "dim",
    "failed": "bold red",
    "unsupported": "dim",
}


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

    if verbose:
        def on_file(p: Path, status: str) -> None:
            style = _STATUS_STYLE.get(status, "")
            _console.print(Text.assemble((f"{status:<11}", style), f"  {p}"))

        stats = index_directory(conn, path, force=force, on_file=on_file)
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            console=_console,
            transient=True,
        ) as progress:
            task = progress.add_task("Scanning…", total=None)

            def on_file(p: Path, status: str) -> None:
                progress.update(task, description=f"[dim]{status}[/dim]  [cyan]{p.name}[/cyan]")

            stats = index_directory(conn, path, force=force, on_file=on_file)

    conn.close()

    parts: list[str] = [f"[green]{stats['indexed']} indexed[/green]"]
    if stats["skipped"]:
        parts.append(f"[dim]{stats['skipped']} skipped[/dim]")
    if stats["unsupported"]:
        parts.append(f"[dim]{stats['unsupported']} unsupported[/dim]")
    if stats["failed"]:
        parts.append(f"[red]{stats['failed']} failed[/red]")

    _console.print("Done — " + "  ".join(parts))
    _console.print(f"[dim]db: {db_path}[/dim]")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@app.command(name="search")
def cmd_search(
    query: str = typer.Argument(..., help="Search query (FTS5 syntax supported)"),
    db: Path | None = typer.Option(None, "--db", help="SQLite database path"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum number of results"),
) -> None:
    """Search the index and print matching files with highlighted snippets."""
    db_path = _db_path(db)

    if not db_path.exists():
        _err.print("[red]No index found.[/red] Run [bold]oasis index <path>[/bold] first.")
        raise typer.Exit(1)

    conn = open_db(db_path)
    try:
        results = KeywordIndex(conn).search(query, limit=limit)
    except sqlite3.OperationalError as exc:
        _err.print(f"[red]Query error:[/red] {exc}")
        _err.print('Tip: wrap phrases in double quotes — e.g. oasis search \\"machine learning\\"')
        raise typer.Exit(1)
    finally:
        conn.close()

    if not results:
        _console.print("[dim]No results.[/dim]")
        return

    cwd = Path.cwd()
    table = Table(box=None, show_header=True, header_style="bold dim", padding=(0, 2))
    table.add_column("#", style="bold", justify="right", no_wrap=True, min_width=2)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Snippet")

    for i, r in enumerate(results, 1):
        try:
            display = r.path.relative_to(cwd)
        except ValueError:
            display = r.path
        file_link = Text(str(display), style=f"cyan link {r.path.resolve().as_uri()}")
        table.add_row(str(i), file_link, r.title or "", _highlight_snippet(r.snippet))

    _console.print(table)
    _console.print(f"\n[dim]{len(results)} result(s)  ·  db: {db_path}[/dim]")
    _save_last_results([r.path for r in results])


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
