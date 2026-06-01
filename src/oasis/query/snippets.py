from __future__ import annotations

import re
import sqlite3

from oasis.index.keyword import MATCH_END, MATCH_START

SNIPPET_TOKENS = 40

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
