"""Tests for oasis.query.parser — ParsedQuery and DateRange models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from oasis.query.parser import DateRange, ParsedQuery

# ---------------------------------------------------------------------------
# DateRange
# ---------------------------------------------------------------------------


def test_date_range_defaults_are_none() -> None:
    dr = DateRange()
    assert dr.after is None
    assert dr.before is None


def test_date_range_after_only() -> None:
    dt = datetime(2024, 1, 1, tzinfo=UTC)
    dr = DateRange(after=dt)
    assert dr.after == dt
    assert dr.before is None


def test_date_range_before_only() -> None:
    dt = datetime(2024, 6, 1, tzinfo=UTC)
    dr = DateRange(before=dt)
    assert dr.before == dt
    assert dr.after is None


def test_date_range_valid_after_before() -> None:
    after = datetime(2024, 1, 1, tzinfo=UTC)
    before = datetime(2024, 12, 31, tzinfo=UTC)
    dr = DateRange(after=after, before=before)
    assert dr.after == after
    assert dr.before == before


def test_date_range_before_equal_to_after_raises() -> None:
    dt = datetime(2024, 6, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        DateRange(after=dt, before=dt)


def test_date_range_before_earlier_than_after_raises() -> None:
    with pytest.raises(ValidationError):
        DateRange(
            after=datetime(2024, 12, 31, tzinfo=UTC),
            before=datetime(2024, 1, 1, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# ParsedQuery — defaults
# ---------------------------------------------------------------------------


def test_parsed_query_minimal() -> None:
    pq = ParsedQuery(semantic_query="quarterly report")
    assert pq.semantic_query == "quarterly report"
    assert pq.file_types == []
    assert pq.date_range is None
    assert pq.folders == []
    assert pq.keywords == []
    assert pq.confidence == 1.0


def test_parsed_query_full() -> None:
    pq = ParsedQuery(
        semantic_query="machine learning notes",
        file_types=[".pdf"],
        date_range=DateRange(after=datetime(2023, 1, 1)),
        folders=["~/Documents"],
        keywords=["neural", "network"],
        confidence=0.85,
    )
    assert pq.semantic_query == "machine learning notes"
    assert pq.file_types == [".pdf"]
    assert pq.date_range is not None
    assert pq.folders == ["~/Documents"]
    assert pq.keywords == ["neural", "network"]
    assert pq.confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# ParsedQuery — semantic_query validation
# ---------------------------------------------------------------------------


def test_semantic_query_stripped() -> None:
    pq = ParsedQuery(semantic_query="  hello world  ")
    assert pq.semantic_query == "hello world"


def test_semantic_query_empty_raises() -> None:
    with pytest.raises(ValidationError):
        ParsedQuery(semantic_query="")


def test_semantic_query_whitespace_only_raises() -> None:
    with pytest.raises(ValidationError):
        ParsedQuery(semantic_query="   ")


# ---------------------------------------------------------------------------
# ParsedQuery — file_types normalisation
# ---------------------------------------------------------------------------


def test_file_types_lowercase() -> None:
    pq = ParsedQuery(semantic_query="q", file_types=["PDF", "DOCX"])
    assert pq.file_types == [".pdf", ".docx"]


def test_file_types_adds_dot_prefix() -> None:
    pq = ParsedQuery(semantic_query="q", file_types=["pdf", "txt"])
    assert pq.file_types == [".pdf", ".txt"]


def test_file_types_already_have_dot() -> None:
    pq = ParsedQuery(semantic_query="q", file_types=[".pdf", ".md"])
    assert pq.file_types == [".pdf", ".md"]


def test_file_types_strips_whitespace() -> None:
    pq = ParsedQuery(semantic_query="q", file_types=["  pdf  "])
    assert pq.file_types == [".pdf"]


def test_file_types_mixed_case_and_dot() -> None:
    pq = ParsedQuery(semantic_query="q", file_types=["PDF", ".Docx", "TXT"])
    assert pq.file_types == [".pdf", ".docx", ".txt"]


def test_file_types_empty_strings_dropped() -> None:
    pq = ParsedQuery(semantic_query="q", file_types=["", "  ", ".pdf"])
    assert pq.file_types == [".pdf"]


def test_file_types_empty_list() -> None:
    pq = ParsedQuery(semantic_query="q", file_types=[])
    assert pq.file_types == []


# ---------------------------------------------------------------------------
# ParsedQuery — confidence bounds
# ---------------------------------------------------------------------------


def test_confidence_zero_valid() -> None:
    pq = ParsedQuery(semantic_query="q", confidence=0.0)
    assert pq.confidence == 0.0


def test_confidence_one_valid() -> None:
    pq = ParsedQuery(semantic_query="q", confidence=1.0)
    assert pq.confidence == 1.0


def test_confidence_above_one_raises() -> None:
    with pytest.raises(ValidationError):
        ParsedQuery(semantic_query="q", confidence=1.1)


def test_confidence_below_zero_raises() -> None:
    with pytest.raises(ValidationError):
        ParsedQuery(semantic_query="q", confidence=-0.1)


def test_confidence_midpoint() -> None:
    pq = ParsedQuery(semantic_query="q", confidence=0.5)
    assert pq.confidence == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# ParsedQuery — date_range integration
# ---------------------------------------------------------------------------


def test_date_range_none_by_default() -> None:
    pq = ParsedQuery(semantic_query="q")
    assert pq.date_range is None


def test_date_range_set() -> None:
    dr = DateRange(after=datetime(2024, 3, 1))
    pq = ParsedQuery(semantic_query="q", date_range=dr)
    assert pq.date_range == dr


def test_date_range_dict_coercion() -> None:
    pq = ParsedQuery(
        semantic_query="q",
        date_range={"after": "2024-01-01T00:00:00"},
    )
    assert pq.date_range is not None
    assert pq.date_range.after == datetime(2024, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------------------
# ParsedQuery — serialisation round-trip
# ---------------------------------------------------------------------------


def test_round_trip_json() -> None:
    pq = ParsedQuery(
        semantic_query="budget docs",
        file_types=[".xlsx"],
        keywords=["Q4", "forecast"],
        confidence=0.9,
    )
    restored = ParsedQuery.model_validate_json(pq.model_dump_json())
    assert restored == pq


def test_model_dump_includes_all_fields() -> None:
    pq = ParsedQuery(semantic_query="q")
    d = pq.model_dump()
    assert set(d.keys()) == {"semantic_query", "file_types", "date_range", "folders", "keywords", "confidence"}


# ---------------------------------------------------------------------------
# DateRange — dateparser coercion (4.4)
# ---------------------------------------------------------------------------


def test_date_range_accepts_iso_string() -> None:
    dr = DateRange(after="2024-01-01T00:00:00")
    assert dr.after == datetime(2024, 1, 1, 0, 0, 0)


def test_date_range_accepts_date_only_string() -> None:
    dr = DateRange(after="2024-03-15")
    assert dr.after is not None
    assert dr.after.year == 2024
    assert dr.after.month == 3
    assert dr.after.day == 15


def test_date_range_accepts_year_string() -> None:
    dr = DateRange(after="2024")
    assert dr.after is not None
    assert dr.after.year == 2024


def test_date_range_accepts_month_year_string() -> None:
    dr = DateRange(after="January 2024")
    assert dr.after is not None
    assert dr.after.year == 2024
    assert dr.after.month == 1


def test_date_range_returns_naive_datetime_from_string() -> None:
    dr = DateRange(after="2024-01-01T00:00:00+00:00")
    assert dr.after is not None
    assert dr.after.tzinfo is None


def test_date_range_none_stays_none() -> None:
    dr = DateRange(after=None)
    assert dr.after is None


def test_date_range_invalid_string_raises() -> None:
    with pytest.raises(ValidationError):
        DateRange(after="not a date at all !!@@##")


def test_date_range_both_fields_coerced() -> None:
    dr = DateRange(after="2024-01-01", before="2025-01-01")
    assert dr.after == datetime(2024, 1, 1)
    assert dr.before == datetime(2025, 1, 1)


def test_date_range_string_before_must_follow_after() -> None:
    with pytest.raises(ValidationError):
        DateRange(after="2025-01-01", before="2024-01-01")
