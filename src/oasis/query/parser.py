from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import dateparser
from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from oasis.llm.base import LLMProvider

_DATEPARSER_SETTINGS = {
    "RETURN_AS_TIMEZONE_AWARE": False,
    "PREFER_DAY_OF_MONTH": "first",
    "PREFER_DATES_FROM": "past",
}

_SYSTEM_PROMPT = """\
You parse a user's natural-language file-search query into a structured JSON object.

Output fields:
- semantic_query (string, REQUIRED): The core topic/concept to search for semantically. Strip
  file-type words, date words, folder words, and filler. Must not be empty.
- file_types (list of strings): File extensions the user wants, lowercase with leading dot
  (e.g. ".pdf", ".pptx", ".xlsx", ".docx", ".py", ".txt", ".jpg"). Infer from common synonyms:
    "powerpoint" / "slides" / "presentation" → [".pptx"]
    "word doc" / "word document" → [".docx"]
    "spreadsheet" / "excel" → [".xlsx"]
    "PDF" → [".pdf"]
    "image" / "photo" → [".jpg", ".png"]
    "code" / "script" / "python" → [".py"]
  Leave empty if no type is mentioned or strongly implied.
- date_range (object or null): {after: ISO datetime or null, before: ISO datetime or null}.
  Use the "Today is …" line to resolve relative expressions:
    "last month" → after = first day of previous calendar month, before = first day of current month
    "this month" → after = first day of current month, before = first day of next month
    "last week"  → after = most-recent Monday, before = today
    "yesterday"  → after = yesterday 00:00, before = today 00:00
    "this year"  → after = Jan 1 of current year, before = Jan 1 of next year
    "last year"  → after = Jan 1 of previous year, before = Jan 1 of current year
    "in 2024"    → after = 2024-01-01, before = 2025-01-01
    "since March" → after = March 1 of current year (or previous year if that is in the future)
  Leave null if no time expression is mentioned.
- folders (list of strings): Subtree paths the user mentioned (e.g. "in ~/Documents", "from the
  Downloads folder"). Expand ~ literally; keep paths as the user wrote them. Leave empty if none.
- keywords (list of strings): Exact words or short phrases that must appear verbatim in the file
  (nouns, proper names, codes, IDs). Do NOT duplicate words already in semantic_query unless they
  are proper nouns or codes that must appear literally. Leave empty if none.
- confidence (float 0–1): How confident you are that you correctly understood the query.
  Use < 0.7 for ambiguous or very short queries.

Rules:
1. Always produce a non-empty semantic_query — it is the fallback even when everything else is filled.
2. Do not hallucinate file types or dates not implied by the query.
3. Dates use ISO 8601 with time 00:00:00 unless stated otherwise.
4. Return ONLY the JSON object — no explanation, no markdown fences.

Examples (today = 2026-06-01):

Query: "meeting notes"
Output: {"semantic_query": "meeting notes", "file_types": [], "date_range": null, "folders": [], "keywords": [], "confidence": 0.95}

Query: "that tax PDF from 2024"
Output: {"semantic_query": "tax documents", "file_types": [".pdf"], "date_range": {"after": "2024-01-01T00:00:00", "before": "2025-01-01T00:00:00"}, "folders": [], "keywords": ["tax"], "confidence": 0.95}

Query: "powerpoints I made last month about ML"
Output: {"semantic_query": "machine learning", "file_types": [".pptx"], "date_range": {"after": "2026-05-01T00:00:00", "before": "2026-06-01T00:00:00"}, "folders": [], "keywords": [], "confidence": 0.9}

Query: "quarterly budget spreadsheets"
Output: {"semantic_query": "quarterly budget", "file_types": [".xlsx"], "date_range": null, "folders": [], "keywords": ["budget"], "confidence": 0.9}

Query: "python scripts in ~/projects"
Output: {"semantic_query": "python scripts", "file_types": [".py"], "date_range": null, "folders": ["~/projects"], "keywords": [], "confidence": 0.95}

Query: "x"
Output: {"semantic_query": "x", "file_types": [], "date_range": null, "folders": [], "keywords": [], "confidence": 0.4}
"""


def parse_query(
    text: str,
    llm: LLMProvider,
    *,
    today: date | None = None,
) -> ParsedQuery:
    """Parse a natural-language search string into a structured ParsedQuery.

    Args:
        text: Raw query string from the user.
        llm:  Any LLMProvider (e.g. OllamaProvider).
        today: Override today's date for testing; defaults to date.today().

    Returns:
        A validated ParsedQuery instance.
    """
    resolved_today = today if today is not None else date.today()
    user_prompt = f"Today is {resolved_today.isoformat()}.\nQuery: {text}"
    return llm.complete(user_prompt, ParsedQuery, system=_SYSTEM_PROMPT)


class DateRange(BaseModel):
    after: datetime | None = None
    before: datetime | None = None

    @field_validator("after", "before", mode="before")
    @classmethod
    def _coerce_date(cls, v: object) -> datetime | None:
        """Accept datetime objects, ISO strings, or any expression dateparser can handle.

        The LLM should produce ISO 8601 strings, but this validator is a safety net
        for relative expressions ("last month", "2024") that slip through.
        """
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime(v.year, v.month, v.day)
        if isinstance(v, str):
            parsed = dateparser.parse(v.strip(), settings=_DATEPARSER_SETTINGS)
            if parsed is None:
                raise ValueError(f"Cannot parse date expression: {v!r}")
            return parsed.replace(tzinfo=None)  # ensure naive datetime
        raise ValueError(f"Expected date string or datetime, got {type(v).__name__}")

    @field_validator("before")
    @classmethod
    def before_must_follow_after(cls, v: datetime | None, info: object) -> datetime | None:
        after = getattr(info, "data", {}).get("after")
        if v is not None and after is not None and v <= after:
            raise ValueError("before must be later than after")
        return v


class ParsedQuery(BaseModel):
    # The distilled natural-language search string passed to the embedding model.
    semantic_query: str

    # e.g. [".pdf", ".docx"] — normalised to lowercase with leading dot.
    file_types: list[str] = Field(default_factory=list)

    date_range: DateRange | None = None

    # Subtree paths the user mentioned (e.g. "in ~/Documents").
    folders: list[str] = Field(default_factory=list)

    # Exact terms that must appear verbatim — fed to FTS5 alongside the semantic query.
    keywords: list[str] = Field(default_factory=list)

    # LLM's self-reported confidence that it correctly understood the query (0–1).
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("file_types", mode="before")
    @classmethod
    def normalise_extensions(cls, v: list[str]) -> list[str]:
        """Ensure every extension is lowercase and starts with a dot."""
        out: list[str] = []
        for ext in v:
            ext = ext.strip().lower()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if ext:
                out.append(ext)
        return out

    @field_validator("semantic_query")
    @classmethod
    def semantic_query_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("semantic_query must not be empty")
        return v.strip()
