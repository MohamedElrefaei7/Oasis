import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from oasis.index.db import open_db
from oasis.index.keyword import MATCH_END, MATCH_START, KeywordIndex
from oasis.index.pipeline import index_directory

app = typer.Typer(help="Oasis — natural-language file search.", no_args_is_help=True)
_console = Console()
_err = Console(stderr=True)


def _default_db() -> Path:
    return Path.home() / ".oasis" / "index.db"


def _highlight_snippet(raw: str) -> Text:
    """Convert MATCH_START/MATCH_END sentinel markers to bold-yellow Rich Text."""
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


@app.command()
def index(
    path: Path = typer.Argument(..., help="Directory to index"),
    db: Path | None = typer.Option(None, "--db", help="SQLite database path"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-index all files ignoring mtime"),
) -> None:
    """Walk PATH and index every supported file into the search database."""
    if not path.is_dir():
        _err.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(1)

    db_path = db or _default_db()
    conn = open_db(db_path)

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        console=_console,
        transient=True,
    ) as progress:
        task = progress.add_task("Starting…", total=None)

        def on_file(p: Path, status: str) -> None:
            progress.update(task, description=f"[dim]{status}[/dim]  [cyan]{p.name}[/cyan]")

        stats = index_directory(conn, path, force=force, on_file=on_file)

    conn.close()

    indexed = stats["indexed"]
    skipped = stats["skipped"]
    failed = stats["failed"]
    unsupported = stats["unsupported"]

    summary = f"[green]{indexed}[/green] indexed"
    if skipped:
        summary += f"  [dim]{skipped} skipped[/dim]"
    if unsupported:
        summary += f"  [dim]{unsupported} unsupported[/dim]"
    if failed:
        summary += f"  [red]{failed} failed[/red]"

    _console.print(f"Done — {summary}")
    _console.print(f"[dim]db: {db_path}[/dim]")


@app.command(name="search")
def cmd_search(
    query: str = typer.Argument(..., help="Search query (FTS5 syntax supported)"),
    db: Path | None = typer.Option(None, "--db", help="SQLite database path"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum number of results"),
) -> None:
    """Search the index and print matching files with highlighted snippets."""
    db_path = db or _default_db()

    if not db_path.exists():
        _err.print("[red]No index found.[/red] Run [bold]oasis index <path>[/bold] first.")
        raise typer.Exit(1)

    conn = open_db(db_path)
    try:
        results = KeywordIndex(conn).search(query, limit=limit)
    except sqlite3.OperationalError as exc:
        _err.print(f"[red]Query error:[/red] {exc}")
        _err.print('Tip: wrap your query in double quotes for an exact phrase — e.g. oasis search \\"machine learning\\"')
        raise typer.Exit(1)
    finally:
        conn.close()

    if not results:
        _console.print("[dim]No results.[/dim]")
        return

    cwd = Path.cwd()
    table = Table(box=None, show_header=True, header_style="bold dim", padding=(0, 2))
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Snippet")

    for r in results:
        try:
            display = r.path.relative_to(cwd)
        except ValueError:
            display = r.path

        table.add_row(str(display), r.title or "", _highlight_snippet(r.snippet))

    _console.print(table)
    _console.print(f"\n[dim]{len(results)} result(s)  ·  db: {db_path}[/dim]")
