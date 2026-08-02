"""Tests for oasis.query.snippets."""

from __future__ import annotations

from oasis.index.keyword import MATCH_END, MATCH_START
from oasis.query.snippets import (
    _extract_terms,
    _highlight_terms,
    text_snippet,
    to_segments,
)

# ---------------------------------------------------------------------------
# _extract_terms
# ---------------------------------------------------------------------------


def test_extract_terms_simple() -> None:
    assert _extract_terms("fox") == ["fox"]


def test_extract_terms_multiple_words() -> None:
    assert _extract_terms("fox cat") == ["fox", "cat"]


def test_extract_terms_strips_and() -> None:
    result = _extract_terms("fox AND cat")
    assert "AND" not in result
    assert "fox" in result
    assert "cat" in result


def test_extract_terms_strips_or() -> None:
    result = _extract_terms("fox OR cat")
    assert "OR" not in result
    assert "fox" in result


def test_extract_terms_strips_not() -> None:
    result = _extract_terms("fox NOT dog")
    assert "NOT" not in result
    assert "fox" in result


def test_extract_terms_strips_near() -> None:
    result = _extract_terms("NEAR(fox cat)")
    assert "NEAR" not in result


def test_extract_terms_quoted_phrase_expanded() -> None:
    result = _extract_terms('"quick fox"')
    assert "quick" in result
    assert "fox" in result


def test_extract_terms_empty_string() -> None:
    assert _extract_terms("") == []


def test_extract_terms_operators_only() -> None:
    assert _extract_terms("AND OR NOT") == []


def test_extract_terms_preserves_case() -> None:
    result = _extract_terms("Hello World")
    assert "Hello" in result
    assert "World" in result


# ---------------------------------------------------------------------------
# _highlight_terms
# ---------------------------------------------------------------------------


def test_highlight_wraps_match() -> None:
    result = _highlight_terms("hello world", ["world"])
    assert result == f"hello {MATCH_START}world{MATCH_END}"


def test_highlight_case_insensitive() -> None:
    result = _highlight_terms("Hello World", ["hello"])
    assert MATCH_START in result
    assert MATCH_END in result


def test_highlight_multiple_terms() -> None:
    result = _highlight_terms("cat and dog", ["cat", "dog"])
    assert result.count(MATCH_START) == 2


def test_highlight_empty_terms_returns_unchanged() -> None:
    text = "nothing to highlight"
    assert _highlight_terms(text, []) == text


def test_highlight_no_match_returns_unchanged() -> None:
    assert _highlight_terms("hello world", ["xyz"]) == "hello world"


def test_highlight_preserves_surrounding_text() -> None:
    result = _highlight_terms("the quick brown fox", ["quick"])
    assert result.startswith("the ")
    assert result.endswith(" brown fox")


def test_highlight_multiple_occurrences() -> None:
    result = _highlight_terms("fox and fox", ["fox"])
    assert result.count(MATCH_START) == 2


# ---------------------------------------------------------------------------
# text_snippet
# ---------------------------------------------------------------------------


def test_text_snippet_returns_string() -> None:
    assert isinstance(text_snippet("hello world", "hello"), str)


def test_text_snippet_short_text_no_ellipsis() -> None:
    result = text_snippet("short text", "short")
    assert not result.startswith("…")
    assert not result.endswith("…")


def test_text_snippet_highlights_matched_term() -> None:
    result = text_snippet("the quick brown fox", "fox")
    assert MATCH_START in result
    assert MATCH_END in result


def test_text_snippet_no_match_starts_from_beginning() -> None:
    result = text_snippet("hello world foo bar", "zzz", length=200)
    assert "hello" in result


def test_text_snippet_empty_query_returns_start_of_text() -> None:
    result = text_snippet("hello world", "")
    assert "hello" in result


def test_text_snippet_truncates_long_text() -> None:
    text = "a" * 500
    result = text_snippet(text, "a", length=200)
    clean = result.replace(MATCH_START, "").replace(MATCH_END, "").replace("…", "")
    assert len(clean) <= 200


def test_text_snippet_trailing_ellipsis_when_text_continues() -> None:
    # Match is at the very start; text is much longer than window.
    text = "needle " + "filler " * 100
    result = text_snippet(text, "needle", length=20)
    assert result.endswith("…")


def test_text_snippet_no_leading_ellipsis_when_match_at_start() -> None:
    text = "needle " + "filler " * 100
    result = text_snippet(text, "needle", length=20)
    assert not result.startswith("…")


def test_text_snippet_leading_ellipsis_when_match_is_deep() -> None:
    # Push the match far into the text so the window doesn't start at 0.
    text = "filler " * 50 + "needle " + "filler " * 50
    result = text_snippet(text, "needle", length=20)
    assert result.startswith("…")


def test_text_snippet_no_trailing_ellipsis_when_text_fits() -> None:
    text = "the fox"
    result = text_snippet(text, "fox", length=200)
    assert not result.endswith("…")


# ---------------------------------------------------------------------------
# to_segments — sentinel string → (text, match) runs
# ---------------------------------------------------------------------------

S, E = MATCH_START, MATCH_END


def _strip(text: str) -> str:
    return text.replace(S, "").replace(E, "")


def _rewrap(segs: list[tuple[str, bool]]) -> str:
    return "".join(f"{S}{t}{E}" if m else t for t, m in segs)


def _assert_invariants(raw: str, segs: list[tuple[str, bool]]) -> None:
    # Concatenation reproduces the input with sentinels removed.
    assert "".join(t for t, _ in segs) == _strip(raw)
    # No empty segments.
    assert all(t for t, _ in segs)
    # No sentinel leaks into any segment.
    assert all(S not in t and E not in t for t, _ in segs)
    # No two adjacent segments share the same match value.
    assert all(a[1] != b[1] for a, b in zip(segs, segs[1:], strict=False))


def test_to_segments_empty_string() -> None:
    assert to_segments("") == []


def test_to_segments_unmatched_is_single_false_segment() -> None:
    assert to_segments("plain text, no markers") == [("plain text, no markers", False)]


def test_to_segments_example_from_spec() -> None:
    raw = f"{S}revenue{E} grew 12% in Q3 driven by {S}enterprise renewals{E}"
    assert to_segments(raw) == [
        ("revenue", True),
        (" grew 12% in Q3 driven by ", False),
        ("enterprise renewals", True),
    ]


def test_to_segments_match_at_start_and_end() -> None:
    assert to_segments(f"{S}hit{E} after") == [("hit", True), (" after", False)]
    assert to_segments(f"before {S}hit{E}") == [("before ", False), ("hit", True)]
    assert to_segments(f"{S}whole{E}") == [("whole", True)]


def test_to_segments_adjacent_matches_merge() -> None:
    # Zero-gap adjacent spans — _highlight_terms produces these for "aa" with
    # term "a" — must merge; rendering is identical either way.
    assert to_segments(f"{S}a{E}{S}b{E}") == [("ab", True)]


def test_to_segments_degenerate_sentinels() -> None:
    assert to_segments(f"{S}{E}") == []  # empty span
    assert to_segments(f"{S}unterminated tail") == [("unterminated tail", False)]
    assert to_segments(f"stray{E} end marker") == [("stray end marker", False)]
    assert to_segments(f"{E}{S}") == []  # reversed pair, no text
    _assert_invariants(f"a{E}b{S}c", to_segments(f"a{E}b{S}c"))


def test_to_segments_unicode() -> None:
    zwj_family = "\U0001f468‍\U0001f469‍\U0001f467"  # 👨‍👩‍👧 (ZWJ sequence)
    combining = "été"  # été with combining accents
    for raw in (
        f"漢字の{S}検索{E}テスト",
        f"launch {S}\U0001f680{E} now",
        f"{S}{zwj_family}{E} family emoji",
        f"caf{S}{combining}{E} combining marks",
    ):
        segs = to_segments(raw)
        _assert_invariants(raw, segs)
        assert _rewrap(segs) == raw  # well-formed + non-adjacent → byte-for-byte


def test_to_segments_roundtrip_property() -> None:
    """Property test over generated inputs (seeded, so deterministic).

    Well-formed, canonically-spaced inputs round-trip byte-for-byte; arbitrary
    sentinel soup still satisfies every structural invariant, and re-parsing
    the re-wrapped output is idempotent.
    """
    import random

    rng = random.Random(20260715)
    alphabet = list("ab xyz.…") + ["漢", "字", "\U0001f680", "‍", "́", "\U0001f469"]

    def rand_text(min_len: int = 1) -> str:
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(min_len, 8)))

    for _ in range(300):
        # Canonical construction: alternating runs, matched runs non-empty,
        # unmatched gaps between matches non-empty (leading/trailing optional).
        runs: list[tuple[str, bool]] = []
        if rng.random() < 0.5:
            runs.append((rand_text(), False))
        for _i in range(rng.randint(1, 4)):
            runs.append((rand_text(), True))
            runs.append((rand_text(), False))
        if rng.random() < 0.5 and runs[-1][1] is False:
            runs.pop()  # allow ending on a match

        # Merge any accidental adjacent-False runs from construction.
        canonical: list[tuple[str, bool]] = []
        for t, m in runs:
            if canonical and canonical[-1][1] == m:
                canonical[-1] = (canonical[-1][0] + t, m)
            else:
                canonical.append((t, m))

        raw = _rewrap(canonical)
        segs = to_segments(raw)
        _assert_invariants(raw, segs)
        assert segs == canonical
        assert _rewrap(segs) == raw

    # Arbitrary sentinel soup: invariants always hold; re-parse is idempotent.
    soup_alphabet = alphabet + [S, E, S, E]
    for _ in range(300):
        raw = "".join(rng.choice(soup_alphabet) for _ in range(rng.randint(0, 24)))
        segs = to_segments(raw)
        _assert_invariants(raw, segs)
        assert to_segments(_rewrap(segs)) == segs
