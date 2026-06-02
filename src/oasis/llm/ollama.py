from __future__ import annotations

from typing import TypeVar

import instructor
import openai
from pydantic import BaseModel

DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_BASE_URL = "http://localhost:11434/v1"

T = TypeVar("T", bound=BaseModel)


class OllamaProvider:
    """Structured-output LLM provider backed by a local Ollama instance.

    Forces instructor's JSON mode so the model emits a JSON object conforming
    to *response_model*'s schema.  Works with any Ollama model that supports
    JSON mode — no function-calling support required.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._model = model
        # openai.OpenAI pointed at the local Ollama endpoint; "ollama" is a
        # placeholder key accepted by the server but never validated.
        self._client = instructor.from_openai(
            openai.OpenAI(base_url=base_url, api_key="ollama"),
            mode=instructor.Mode.JSON,
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
