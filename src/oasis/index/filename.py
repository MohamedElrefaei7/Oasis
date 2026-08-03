"""Turning a file's name into words a search engine can match.

A filename is the one piece of metadata a person *chose*, and it is often the
only place the thing they'll search for is written down: `okapi` appears in
`paper-okapi-at-trec3.pdf` and nowhere in the OCR'd body. Oasis stored the raw
path in FTS from the start, which sounds like it covers this and doesn't —
`unicode61` splits on punctuation but not on case or letter/digit boundaries,
so `Q3Report.pdf` and `trec3` are single opaque tokens that no reasonable query
matches.

This module is the normalization both arms share: the keyword index stores its
output in the ``filename`` FTS column, the pipeline embeds it as a chunk, and
the reranker prepends it to the passage. One function, so the three can't
disagree about what a filename "says".
"""

from __future__ import annotations

import re
from pathlib import Path

# Split on any run of non-alphanumerics, and on the case boundary unicode61 has
# no concept of: `Q3ReportFinal` → `Q3 Report Final`. The lookahead form (rather
# than a plain split) is what keeps the boundary characters themselves, since
# both sides are real content.
_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_RUNS = re.compile(r"[0-9]+|[A-Za-z]+")

# A letter run this short glued to digits is one word, not two: `q3`, `v2`,
# `h1`, `mp3`. Longer, and the digits read as a separate number — `trec3` is
# "trec" then "3", which is the whole reason to split at all. There is no
# principled boundary here, only a useful one; 3 keeps every short label
# people actually use (quarter, version, half, format) intact.
_GLUE_MAX_LETTERS = 2


def filename_words(name: str) -> list[str]:
    """Split one filename component into its words, order preserved.

    Case is left alone: FTS5 folds it, the embedder is uncased, and the
    reranker reads better with the original. Empty pieces are dropped, so a
    name that is all punctuation yields ``[]`` rather than a list of blanks.

    A digit run **after** a short letter run is glued back on
    (``_GLUE_MAX_LETTERS``); a letter run after digits never is, so
    ``2023taxes`` yields a findable ``taxes``.
    """
    words: list[str] = []
    for piece in _SEPARATORS.split(name):
        for part in _CAMEL.split(piece):
            # Gluing is scoped to one `part`: a separator the user typed
            # (`q-3`) is a boundary they meant, and must not be undone here.
            runs: list[str] = []
            for run in _RUNS.findall(part):
                if (
                    runs
                    and run[0].isdigit()
                    and runs[-1][0].isalpha()
                    and len(runs[-1]) <= _GLUE_MAX_LETTERS
                ):
                    runs[-1] += run
                else:
                    runs.append(run)
            words.extend(runs)
    return words


def humanize_filename(path: Path | str) -> str:
    """The searchable text of *path*'s own name — no directories, no extension.

    ``/Users/you/tax/2023TaxReturn-final.v2.pdf`` → ``"2023 Tax Return final v2"``.

    Directories are deliberately excluded. They are already in the ``path`` FTS
    column, and folding them in here would put every token of the user's home
    directory into the strongest-weighted column of every document they own —
    which is not a filename signal, it's a constant.

    Only the *last* suffix is dropped, so `report.tar.gz` keeps `tar`. The
    extension itself is dropped because it is a structured field twice over
    (``documents.extension`` and the ``file_types`` filter); leaving it in the
    text would let `pdf` win on BM25 against documents genuinely about PDFs.
    """
    stem = Path(path).name
    suffix = Path(stem).suffix
    if suffix and suffix != stem:
        stem = stem[: -len(suffix)]
    return " ".join(filename_words(stem))
