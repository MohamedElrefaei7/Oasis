from __future__ import annotations

import os
from typing import TypeVar

import anthropic
import instructor
from pydantic import BaseModel

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 1024

T = TypeVar("T", bound=BaseModel)


class ClaudeProvider:
    """Structured-output LLM provider backed by Anthropic's Claude API.

    Uses instructor in ANTHROPIC_TOOLS mode: Claude is given a tool whose
    schema matches *response_model*, then instructor validates the tool-call
    arguments into the Pydantic instance.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = instructor.from_anthropic(
            anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")),
            mode=instructor.Mode.ANTHROPIC_TOOLS,
        )

    def complete(
        self,
        prompt: str,
        response_model: type[T],
        *,
        system: str | None = None,
    ) -> T:
        messages = _build_messages(prompt, system)
        return self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            response_model=response_model,
            max_tokens=self._max_tokens,
        )


def _build_messages(
    prompt: str,
    system: str | None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages
