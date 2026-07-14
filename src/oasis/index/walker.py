import os
from collections.abc import Callable, Generator
from pathlib import Path

import pathspec

# Directories pruned by name at any depth before pathspec runs.
# Fast O(1) set lookup — no pattern matching overhead for the common cases.
_DIR_EXCLUDES: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    ".eggs",
})

# File-level gitwildmatch patterns applied everywhere regardless of .gitignore.
_DEFAULT_FILE_PATTERNS: list[str] = [
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
]


def walk(
    root: Path,
    *,
    extra_excludes: list[str] | None = None,
    respect_gitignore: bool = True,
    exclude_dotfiles: bool = True,
    on_error: Callable[[OSError], None] | None = None,
) -> Generator[Path]:
    """Yield every non-excluded file under root as a generator.

    Exclusion is applied in three layers, cheapest first:
    1. _DIR_EXCLUDES — hard-coded directory names checked before any pattern
       matching.  Pruned in-place so os.walk never descends into them.
    2. Dotfile/dotdir skip — names starting with '.' when exclude_dotfiles=True.
    3. pathspec spec — _DEFAULT_FILE_PATTERNS + extra_excludes + the root-level
       .gitignore (when respect_gitignore=True).

    *on_error* is forwarded to os.walk's ``onerror``.  Without it os.walk
    silently swallows directory-level errors, so an unreadable tree yields
    nothing and looks identical to an empty one — which is exactly what macOS
    reports when Full Disk Access has not been granted.  Callers that need to
    tell "no files" apart from "not allowed to look" must pass this.

    Note: only the root-level .gitignore is loaded.  Nested .gitignore files
    are not yet supported.
    """
    patterns: list[str] = list(_DEFAULT_FILE_PATTERNS)
    if extra_excludes:
        patterns.extend(extra_excludes)
    if respect_gitignore:
        gitignore = root / ".gitignore"
        if gitignore.is_file():
            try:
                lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
                patterns.extend(lines)
            except OSError:
                pass

    spec = pathspec.PathSpec.from_lines("gitignore", patterns)

    for dirpath_str, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=on_error
    ):
        dirpath = Path(dirpath_str)
        rel_dir = dirpath.relative_to(root)

        # Prune dirnames in-place — os.walk will not descend into removed entries.
        dirnames[:] = [
            d for d in sorted(dirnames)
            if d not in _DIR_EXCLUDES
            and not (exclude_dotfiles and d.startswith("."))
            and not spec.match_file((rel_dir / d).as_posix() + "/")
        ]

        for filename in sorted(filenames):
            if exclude_dotfiles and filename.startswith("."):
                continue
            rel_file = rel_dir / filename
            if not spec.match_file(rel_file.as_posix()):
                yield dirpath / filename
