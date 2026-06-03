"""Tests for oasis.llm.ollama provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from oasis.llm.base import LLMProvider
from oasis.llm.ollama import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OllamaProvider,
)
from oasis.query.parser import ParsedQuery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Simple(BaseModel):
    answer: str


def _parsed() -> ParsedQuery:
    return ParsedQuery(semantic_query="machine learning notes")


def _fake_client(return_value: object = None) -> MagicMock:
    m = MagicMock()
    m.chat.completions.create.return_value = return_value
    return m


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def ollama_fixture():
    """(OllamaProvider, fake_instructor_client) with from_openai + OpenAI patched."""
    fake = _fake_client(_parsed())
    with patch("oasis.llm.ollama.instructor.from_openai", return_value=fake), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        provider = OllamaProvider()
    return provider, fake


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_ollama_satisfies_llmprovider_protocol() -> None:
    assert isinstance(OllamaProvider.__new__(OllamaProvider), LLMProvider)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_ollama_default_model() -> None:
    assert DEFAULT_MODEL == "llama3.2:3b"


def test_ollama_default_base_url() -> None:
    assert DEFAULT_BASE_URL == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_ollama_constructs_with_defaults() -> None:
    fake = _fake_client()
    with patch("oasis.llm.ollama.instructor.from_openai", return_value=fake), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        p = OllamaProvider()
    assert p._model == DEFAULT_MODEL


def test_ollama_accepts_custom_model() -> None:
    fake = _fake_client()
    with patch("oasis.llm.ollama.instructor.from_openai", return_value=fake), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        p = OllamaProvider(model="phi3:mini")
    assert p._model == "phi3:mini"


def test_ollama_accepts_custom_base_url() -> None:
    fake = _fake_client()
    with patch("oasis.llm.ollama.instructor.from_openai", return_value=fake), \
         patch("oasis.llm.ollama.openai.OpenAI") as mock_oai:
        OllamaProvider(base_url="http://remote:11434/v1")
    mock_oai.assert_called_once_with(base_url="http://remote:11434/v1", api_key="ollama")


def test_ollama_uses_json_mode() -> None:
    import instructor as _instructor
    with patch("oasis.llm.ollama.instructor.from_openai") as mock_foi, \
         patch("oasis.llm.ollama.openai.OpenAI"):
        mock_foi.return_value = _fake_client()
        OllamaProvider()
    _, kwargs = mock_foi.call_args
    assert kwargs.get("mode") == _instructor.Mode.JSON


def test_ollama_uses_placeholder_api_key() -> None:
    with patch("oasis.llm.ollama.instructor.from_openai") as mock_foi, \
         patch("oasis.llm.ollama.openai.OpenAI") as mock_oai:
        mock_foi.return_value = _fake_client()
        OllamaProvider()
    _, kwargs = mock_oai.call_args
    assert kwargs["api_key"] == "ollama"


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


def test_ollama_complete_returns_response_model(ollama_fixture: tuple) -> None:
    provider, _ = ollama_fixture
    result = provider.complete("find ML docs", ParsedQuery)
    assert isinstance(result, ParsedQuery)


def test_ollama_complete_calls_create_once(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    provider.complete("q", ParsedQuery)
    fake.chat.completions.create.assert_called_once()


def test_ollama_complete_passes_response_model(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    provider.complete("q", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["response_model"] is ParsedQuery


def test_ollama_complete_prompt_is_last_user_message(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    provider.complete("my local query", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    messages = kwargs["messages"]
    assert messages[-1] == {"role": "user", "content": "my local query"}


def test_ollama_complete_without_system_has_one_message(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    provider.complete("q", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    assert len(kwargs["messages"]) == 1


def test_ollama_complete_with_system_has_two_messages(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    provider.complete("q", ParsedQuery, system="You extract structured data.")
    _, kwargs = fake.chat.completions.create.call_args
    assert len(kwargs["messages"]) == 2


def test_ollama_complete_system_is_first_message(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    provider.complete("q", ParsedQuery, system="sys")
    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}


def test_ollama_complete_passes_model(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    provider.complete("q", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["model"] == DEFAULT_MODEL


def test_ollama_complete_works_with_any_response_model(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    fake.chat.completions.create.return_value = _Simple(answer="42")
    result = provider.complete("q", _Simple)
    assert isinstance(result, _Simple)
