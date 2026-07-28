"""Frozen-binary entry point for `oasis serve` — disposable spike launcher.

Drop-in for what the Swift app spawns: argv is passed through untouched, so
`--port` / `--db` / `--managed` behave exactly as they do under the CLI.

The one thing that must happen before anything else is
`multiprocessing.freeze_support()`. Frozen torch's resource-tracker re-execs
the binary; PyInstaller only diverts that re-exec if the app has called
freeze_support(). Without it the binary respawn-loops instead of serving.
Calling it *before* importing the engine keeps diverted helper processes from
paying a full torch import on their way to being told they are helpers.
"""

from __future__ import annotations

import multiprocessing
import sys


def _main() -> None:
    multiprocessing.freeze_support()

    # Imported after freeze_support() on purpose — see module docstring.
    from oasis.cli.app import app

    # typer/click reads sys.argv[1:] itself; force the serve subcommand so the
    # frozen binary *is* the server, while still honouring passed-through flags.
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["serve", *argv]
    app(args=argv, prog_name="oasis-serve", standalone_mode=True)


if __name__ == "__main__":
    _main()
