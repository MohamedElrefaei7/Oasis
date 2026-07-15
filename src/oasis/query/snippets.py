from __future__ import annotations

import re
import sqlite3

from oasis.index.keyword import MATCH_END, MATCH_START

SNIPPET_TOKENS = 40

# A well-formed highlight span: MATCH_START … MATCH_END, shortest match.
_SPAN_RE = re.compile(f"{MATCH_START}(.*?){MATCH_END}", flags=re.DOTALL)


def to_segments(marked_text: str) -> list[tuple[str, bool]]:
    """Split a MATCH_START/MATCH_END-marked string into ordered (text, match) runs.

    The wire format for snippets (CLAUDE.md § Snippet format): segments, not
    offsets. Guarantees, for any input:
    - concatenating every text reproduces the input with sentinels stripped;
    - no empty segments;
    - no two adjacent segments share the same ``match`` value (merged);
    - an unmatched string is a single ``(text, False)``; empty string → ``[]``.

    Only well-formed spans count as matches; stray or unterminated sentinels
    are stripped and their text treated as unmatched (same as the CLI's
    renderer). Zero-gap adjacent spans merge into one matched segment, so the
    sentinel round-trip is canonical, not byte-for-byte, for those inputs.
    """
    segments: list[tuple[str, bool]] = []

    def emit(text: str, match: bool) -> None:
        # A non-greedy span can still capture a nested MATCH_START; strip
        # sentinels from every run so concatenation is exactly the de-marked input.
        text = text.replace(MATCH_START, "").replace(MATCH_END, "")
        if not text:
            return
        if segments and segments[-1][1] == match:
            segments[-1] = (segments[-1][0] + text, match)
        else:
            segments.append((text, match))

    pos = 0
    for m in _SPAN_RE.finditer(marked_text):
        emit(marked_text[pos : m.start()], False)
        emit(m.group(1), True)
        pos = m.end()
    emit(marked_text[pos:], False)
    return segments

_FTS_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})
_TERM_RE = re.compile(r'"[^"]*"|\b\w+\b')


def _extract_terms(query: str) -> list[str]:
    """Return plain word tokens from an FTS5 query expression, dropping boolean operators."""
    tokens: list[str] = []
    for m in _TERM_RE.finditer(query):
        tok = m.group(0)
        if tok.startswith('"'):
            tokens.extend(w for w in tok[1:-1].split() if w.upper() not in _FTS_OPERATORS)
        elif tok.upper() not in _FTS_OPERATORS:
            tokens.append(tok)
    return tokens


def _highlight_terms(text: str, terms: list[str]) -> str:
    """Wrap occurrences of *terms* with MATCH_START/MATCH_END (case-insensitive)."""
    if not terms:
        return text
    pattern = "|".join(re.escape(t) for t in terms)
    return re.sub(
        pattern,
        lambda m: f"{MATCH_START}{m.group(0)}{MATCH_END}",
        text,
        flags=re.IGNORECASE,
    )


def fts_snippet(
    conn: sqlite3.Connection,
    query: str,
    doc_id: int,
    *,
    num_tokens: int = SNIPPET_TOKENS,
) -> str | None:
    """Return an FTS5-generated snippet for *doc_id*, or None if unavailable."""
    try:
        row = conn.execute(
            """
            SELECT snippet(documents_fts, 2, char(2), char(3), '…', ?) AS snip
            FROM documents_fts
            WHERE documents_fts MATCH ? AND rowid = ?
            """,
            (num_tokens, query, doc_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["snip"] if row else None


def text_snippet(text: str, query: str, *, length: int = 200) -> str:
    """Pure-Python fallback: a *length*-char window centered on the first term match."""
    terms = _extract_terms(query)
    start = 0
    if terms:
        pattern = "|".join(re.escape(t) for t in terms)
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            start = max(0, m.start() - length // 2)

    end = start + length
    excerpt = text[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + _highlight_terms(excerpt, terms) + suffix


def get_snippet(
    conn: sqlite3.Connection,
    query: str,
    doc_id: int,
    fallback_text: str,
    *,
    length: int = 200,
) -> str:
    """Return an FTS5 snippet when available, otherwise a plain-text excerpt."""
    result = fts_snippet(conn, query, doc_id)
    if result is not None:
        return result
    return text_snippet(fallback_text, query, length=length)
