"""Tests for oasis.llm.claude and oasis.llm.ollama providers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from oasis.llm.base import LLMProvider
from oasis.llm.claude import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, ClaudeProvider
from oasis.llm.ollama import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL,
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def claude_fixture():
    """(ClaudeProvider, fake_instructor_client) with from_anthropic patched."""
    fake = _fake_client(_parsed())
    with patch("oasis.llm.claude.instructor.from_anthropic", return_value=fake):
        provider = ClaudeProvider()
    return provider, fake


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


def test_claude_satisfies_llmprovider_protocol() -> None:
    assert isinstance(ClaudeProvider.__new__(ClaudeProvider), LLMProvider)


def test_ollama_satisfies_llmprovider_protocol() -> None:
    assert isinstance(OllamaProvider.__new__(OllamaProvider), LLMProvider)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_claude_default_model() -> None:
    assert DEFAULT_MODEL == "claude-sonnet-4-6"


def test_claude_default_max_tokens() -> None:
    assert DEFAULT_MAX_TOKENS == 1024


def test_ollama_default_model() -> None:
    assert OLLAMA_DEFAULT_MODEL == "llama3.2:3b"


def test_ollama_default_base_url() -> None:
    assert DEFAULT_BASE_URL == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# ClaudeProvider — construction
# ---------------------------------------------------------------------------


def test_claude_constructs_with_defaults() -> None:
    fake = _fake_client()
    with patch("oasis.llm.claude.instructor.from_anthropic", return_value=fake):
        p = ClaudeProvider()
    assert p._model == DEFAULT_MODEL
    assert p._max_tokens == DEFAULT_MAX_TOKENS


def test_claude_accepts_custom_model() -> None:
    fake = _fake_client()
    with patch("oasis.llm.claude.instructor.from_anthropic", return_value=fake):
        p = ClaudeProvider(model="claude-opus-4-7")
    assert p._model == "claude-opus-4-7"


def test_claude_accepts_custom_max_tokens() -> None:
    fake = _fake_client()
    with patch("oasis.llm.claude.instructor.from_anthropic", return_value=fake):
        p = ClaudeProvider(max_tokens=2048)
    assert p._max_tokens == 2048


def test_claude_uses_anthropic_tools_mode() -> None:
    import instructor as _instructor
    with patch("oasis.llm.claude.instructor.from_anthropic") as mock_fa, \
         patch("oasis.llm.claude.anthropic.Anthropic"):
        mock_fa.return_value = _fake_client()
        ClaudeProvider()
    _, kwargs = mock_fa.call_args
    assert kwargs.get("mode") == _instructor.Mode.ANTHROPIC_TOOLS


def test_claude_forwards_api_key() -> None:
    with patch("oasis.llm.claude.instructor.from_anthropic") as mock_fa, \
         patch("oasis.llm.claude.anthropic.Anthropic") as mock_ant:
        mock_fa.return_value = _fake_client()
        ClaudeProvider(api_key="sk-test-key")
    mock_ant.assert_called_once_with(api_key="sk-test-key")


def test_claude_uses_none_api_key_when_not_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("oasis.llm.claude.instructor.from_anthropic") as mock_fa, \
         patch("oasis.llm.claude.anthropic.Anthropic") as mock_ant:
        mock_fa.return_value = _fake_client()
        ClaudeProvider()
    mock_ant.assert_called_once_with(api_key=None)


# ---------------------------------------------------------------------------
# ClaudeProvider — complete()
# ---------------------------------------------------------------------------


def test_claude_complete_returns_response_model(claude_fixture: tuple) -> None:
    provider, _ = claude_fixture
    result = provider.complete("find ML docs", ParsedQuery)
    assert isinstance(result, ParsedQuery)


def test_claude_complete_calls_create_once(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("query", ParsedQuery)
    fake.chat.completions.create.assert_called_once()


def test_claude_complete_passes_response_model(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("query", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["response_model"] is ParsedQuery


def test_claude_complete_prompt_is_last_user_message(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("my search query", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    messages = kwargs["messages"]
    assert messages[-1] == {"role": "user", "content": "my search query"}


def test_claude_complete_without_system_has_one_message(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("q", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    assert len(kwargs["messages"]) == 1


def test_claude_complete_with_system_has_two_messages(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("q", ParsedQuery, system="You are a helpful assistant.")
    _, kwargs = fake.chat.completions.create.call_args
    messages = kwargs["messages"]
    assert len(messages) == 2


def test_claude_complete_system_is_first_message(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("q", ParsedQuery, system="sys prompt")
    _, kwargs = fake.chat.completions.create.call_args
    messages = kwargs["messages"]
    assert messages[0] == {"role": "system", "content": "sys prompt"}


def test_claude_complete_passes_model(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("q", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["model"] == DEFAULT_MODEL


def test_claude_complete_passes_max_tokens(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    provider.complete("q", ParsedQuery)
    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS


def test_claude_complete_works_with_any_response_model(claude_fixture: tuple) -> None:
    provider, fake = claude_fixture
    fake.chat.completions.create.return_value = _Simple(answer="yes")
    result = provider.complete("q", _Simple)
    assert isinstance(result, _Simple)


# ---------------------------------------------------------------------------
# OllamaProvider — construction
# ---------------------------------------------------------------------------


def test_ollama_constructs_with_defaults() -> None:
    fake = _fake_client()
    with patch("oasis.llm.ollama.instructor.from_openai", return_value=fake), \
         patch("oasis.llm.ollama.openai.OpenAI"):
        p = OllamaProvider()
    assert p._model == OLLAMA_DEFAULT_MODEL


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
# OllamaProvider — complete()
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
    assert kwargs["model"] == OLLAMA_DEFAULT_MODEL


def test_ollama_complete_works_with_any_response_model(ollama_fixture: tuple) -> None:
    provider, fake = ollama_fixture
    fake.chat.completions.create.return_value = _Simple(answer="42")
    result = provider.complete("q", _Simple)
    assert isinstance(result, _Simple)


# ---------------------------------------------------------------------------
# Provider symmetry — same calling convention
# ---------------------------------------------------------------------------


def test_both_providers_accept_system_kwarg(
    claude_fixture: tuple, ollama_fixture: tuple
) -> None:
    cp, _ = claude_fixture
    op, _ = ollama_fixture
    # Neither should raise when system= is provided
    cp.complete("q", ParsedQuery, system="sys")
    op.complete("q", ParsedQuery, system="sys")


def test_both_providers_work_without_system(
    claude_fixture: tuple, ollama_fixture: tuple
) -> None:
    cp, _ = claude_fixture
    op, _ = ollama_fixture
    cp.complete("q", ParsedQuery)
    op.complete("q", ParsedQuery)
