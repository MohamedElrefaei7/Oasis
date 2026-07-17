"""Regression tests for parse_query().

Each test drives a fake LLM that returns a fixed ParsedQuery and verifies that
parse_query() correctly assembles the user prompt and that the returned object
matches expectations.  The LLM prompt itself is also validated so accidental
system-prompt regressions are caught early.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TypeVar
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from oasis.query.parser import _SYSTEM_PROMPT, DateRange, ParsedQuery, parse_query

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)

TODAY = date(2026, 6, 1)


def _llm(return_value: ParsedQuery) -> MagicMock:
    """Fake LLMProvider whose complete() echoes *return_value*."""
    m = MagicMock()
    m.complete.return_value = return_value
    return m


def _last_call_kwargs(llm: MagicMock) -> dict:
    _, kw = llm.complete.call_args
    return kw


def _last_call_args(llm: MagicMock) -> tuple:
    args, _ = llm.complete.call_args
    return args


# ---------------------------------------------------------------------------
# Prompt-structure tests
# ---------------------------------------------------------------------------


def test_parse_query_passes_system_prompt() -> None:
    llm = _llm(ParsedQuery(semantic_query="test"))
    parse_query("test", llm, today=TODAY)
    assert _last_call_kwargs(llm).get("system") == _SYSTEM_PROMPT


def test_parse_query_passes_parsedquery_class() -> None:
    llm = _llm(ParsedQuery(semantic_query="test"))
    parse_query("test", llm, today=TODAY)
    assert _last_call_args(llm)[1] is ParsedQuery


def test_parse_query_user_prompt_contains_today() -> None:
    llm = _llm(ParsedQuery(semantic_query="test"))
    parse_query("notes", llm, today=TODAY)
    prompt = _last_call_args(llm)[0]
    assert "2026-06-01" in prompt


def test_parse_query_user_prompt_contains_query_text() -> None:
    llm = _llm(ParsedQuery(semantic_query="test"))
    parse_query("budget spreadsheets", llm, today=TODAY)
    prompt = _last_call_args(llm)[0]
    assert "budget spreadsheets" in prompt


def test_parse_query_uses_date_today_by_default() -> None:
    """When today= is omitted, the prompt still contains a date line."""
    llm = _llm(ParsedQuery(semantic_query="test"))
    parse_query("anything", llm)
    prompt = _last_call_args(llm)[0]
    assert prompt.startswith("Today is ")


def test_parse_query_today_override_is_respected() -> None:
    llm = _llm(ParsedQuery(semantic_query="test"))
    parse_query("x", llm, today=date(2024, 3, 15))
    prompt = _last_call_args(llm)[0]
    assert "2024-03-15" in prompt


def test_parse_query_query_label_present() -> None:
    llm = _llm(ParsedQuery(semantic_query="test"))
    parse_query("find my notes", llm, today=TODAY)
    prompt = _last_call_args(llm)[0]
    assert "Query:" in prompt


def test_parse_query_system_prompt_mentions_semantic_query() -> None:
    assert "semantic_query" in _SYSTEM_PROMPT


def test_parse_query_system_prompt_mentions_file_types() -> None:
    assert "file_types" in _SYSTEM_PROMPT


def test_parse_query_system_prompt_mentions_date_range() -> None:
    assert "date_range" in _SYSTEM_PROMPT


def test_parse_query_system_prompt_has_examples() -> None:
    assert "meeting notes" in _SYSTEM_PROMPT


def test_parse_query_system_prompt_explains_last_month() -> None:
    assert "last month" in _SYSTEM_PROMPT


def test_parse_query_system_prompt_has_pptx_hint() -> None:
    assert ".pptx" in _SYSTEM_PROMPT


def test_parse_query_system_prompt_has_pdf_hint() -> None:
    assert ".pdf" in _SYSTEM_PROMPT


def test_parse_query_returns_parsedquery_instance() -> None:
    expected = ParsedQuery(semantic_query="meeting notes")
    llm = _llm(expected)
    result = parse_query("meeting notes", llm, today=TODAY)
    assert isinstance(result, ParsedQuery)


def test_parse_query_returns_llm_output_verbatim() -> None:
    expected = ParsedQuery(
        semantic_query="machine learning",
        file_types=[".pptx"],
        date_range=DateRange(
            after=datetime(2026, 5, 1), before=datetime(2026, 6, 1)
        ),
    )
    llm = _llm(expected)
    result = parse_query("powerpoints about ML last month", llm, today=TODAY)
    assert result is expected


# ---------------------------------------------------------------------------
# Regression cases — each verifies the object the LLM returns is well-formed
# and that parse_query() passes it through unchanged.
# ---------------------------------------------------------------------------

_CASES: list[tuple[str, ParsedQuery]] = [
    # 1 — bare topic, no extras
    (
        "meeting notes",
        ParsedQuery(semantic_query="meeting notes"),
    ),
    # 2 — file type only
    (
        "PDFs about machine learning",
        ParsedQuery(semantic_query="machine learning", file_types=[".pdf"]),
    ),
    # 3 — file type + year date + keyword
    (
        "that tax PDF from 2024",
        ParsedQuery(
            semantic_query="tax documents",
            file_types=[".pdf"],
            date_range=DateRange(
                after=datetime(2024, 1, 1), before=datetime(2025, 1, 1)
            ),
            keywords=["tax"],
        ),
    ),
    # 4 — slides + last month + topic
    (
        "PowerPoints about ML last month",
        ParsedQuery(
            semantic_query="machine learning",
            file_types=[".pptx"],
            date_range=DateRange(
                after=datetime(2026, 5, 1), before=datetime(2026, 6, 1)
            ),
        ),
    ),
    # 5 — spreadsheet + keyword
    (
        "quarterly budget spreadsheets",
        ParsedQuery(
            semantic_query="quarterly budget",
            file_types=[".xlsx"],
            keywords=["budget"],
        ),
    ),
    # 6 — folder hint
    (
        "python scripts in ~/projects",
        ParsedQuery(
            semantic_query="python scripts",
            file_types=[".py"],
            folders=["~/projects"],
        ),
    ),
    # 7 — this year
    (
        "invoices this year",
        ParsedQuery(
            semantic_query="invoices",
            date_range=DateRange(
                after=datetime(2026, 1, 1), before=datetime(2027, 1, 1)
            ),
            keywords=["invoice"],
        ),
    ),
    # 8 — last year
    (
        "project plans from last year",
        ParsedQuery(
            semantic_query="project plans",
            date_range=DateRange(
                after=datetime(2025, 1, 1), before=datetime(2026, 1, 1)
            ),
        ),
    ),
    # 9 — Word documents
    (
        "word documents about onboarding",
        ParsedQuery(semantic_query="onboarding", file_types=[".docx"]),
    ),
    # 10 — yesterday
    (
        "files I edited yesterday",
        ParsedQuery(
            semantic_query="edited files",
            date_range=DateRange(
                after=datetime(2026, 5, 31), before=datetime(2026, 6, 1)
            ),
        ),
    ),
    # 11 — folder + file type
    (
        "PDFs in ~/Downloads",
        ParsedQuery(
            semantic_query="documents",
            file_types=[".pdf"],
            folders=["~/Downloads"],
        ),
    ),
    # 12 — multiple file types
    (
        "images and photos from last month",
        ParsedQuery(
            semantic_query="images photos",
            file_types=[".jpg", ".png"],
            date_range=DateRange(
                after=datetime(2026, 5, 1), before=datetime(2026, 6, 1)
            ),
        ),
    ),
    # 13 — proper noun keyword
    (
        "documents mentioning Alice",
        ParsedQuery(semantic_query="documents mentioning Alice", keywords=["Alice"]),
    ),
    # 14 — this month
    (
        "notes from this month",
        ParsedQuery(
            semantic_query="notes",
            date_range=DateRange(
                after=datetime(2026, 6, 1), before=datetime(2026, 7, 1)
            ),
        ),
    ),
    # 15 — ambiguous single word → low confidence
    (
        "x",
        ParsedQuery(semantic_query="x", confidence=0.4),
    ),
    # 16 — code file type
    (
        "Python scripts that parse JSON",
        ParsedQuery(
            semantic_query="parse JSON",
            file_types=[".py"],
            keywords=["JSON"],
        ),
    ),
    # 17 — since a named month
    (
        "reports since March",
        ParsedQuery(
            semantic_query="reports",
            date_range=DateRange(
                after=datetime(2026, 3, 1), before=None
            ),
        ),
    ),
    # 18 — explicit folder path
    (
        "spreadsheets in ~/Documents/Finance",
        ParsedQuery(
            semantic_query="financial spreadsheets",
            file_types=[".xlsx"],
            folders=["~/Documents/Finance"],
        ),
    ),
    # 19 — presentation + keyword + date
    (
        "slide deck about GDPR compliance from 2023",
        ParsedQuery(
            semantic_query="GDPR compliance",
            file_types=[".pptx"],
            date_range=DateRange(
                after=datetime(2023, 1, 1), before=datetime(2024, 1, 1)
            ),
            keywords=["GDPR"],
        ),
    ),
    # 20 — research notes, no structured hints
    (
        "research notes on neural networks",
        ParsedQuery(semantic_query="neural networks research notes"),
    ),
    # 21 — last week
    (
        "documents from last week",
        ParsedQuery(
            semantic_query="documents",
            date_range=DateRange(
                after=datetime(2026, 5, 25), before=datetime(2026, 6, 1)
            ),
        ),
    ),
    # 22 — multi-keyword
    (
        "contract signed by Bob",
        ParsedQuery(
            semantic_query="contract",
            keywords=["contract", "Bob"],
        ),
    ),
    # 23 — photo in a specific folder
    (
        "vacation photos from ~/Pictures/2025",
        ParsedQuery(
            semantic_query="vacation photos",
            file_types=[".jpg", ".png"],
            folders=["~/Pictures/2025"],
        ),
    ),
    # 24 — mixed: all fields populated
    (
        "Q3 budget Excel in ~/Finance last year",
        ParsedQuery(
            semantic_query="Q3 budget",
            file_types=[".xlsx"],
            date_range=DateRange(
                after=datetime(2025, 1, 1), before=datetime(2026, 1, 1)
            ),
            folders=["~/Finance"],
            keywords=["Q3", "budget"],
        ),
    ),
    # 25 — low-information query
    (
        "stuff",
        ParsedQuery(semantic_query="stuff", confidence=0.5),
    ),
]


@pytest.mark.parametrize("query,expected", _CASES, ids=[c[0][:40] for c in _CASES])
def test_parse_query_regression(query: str, expected: ParsedQuery) -> None:
    """parse_query must pass through whatever the LLM returns unchanged."""
    llm = _llm(expected)
    result = parse_query(query, llm, today=TODAY)
    assert result is expected


@pytest.mark.parametrize("query,expected", _CASES, ids=[c[0][:40] for c in _CASES])
def test_parse_query_calls_llm_once(query: str, expected: ParsedQuery) -> None:
    llm = _llm(expected)
    parse_query(query, llm, today=TODAY)
    llm.complete.assert_called_once()


@pytest.mark.parametrize("query,expected", _CASES, ids=[c[0][:40] for c in _CASES])
def test_parse_query_user_prompt_has_query(query: str, expected: ParsedQuery) -> None:
    llm = _llm(expected)
    parse_query(query, llm, today=TODAY)
    prompt = _last_call_args(llm)[0]
    assert query in prompt
