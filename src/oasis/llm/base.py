from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    """Structural interface for LLM backends that return typed Pydantic objects.

    Both sync implementations (ClaudeProvider, OllamaProvider) satisfy this
    Protocol without inheriting from it — duck typing only.
    """

    def complete(
        self,
        prompt: str,
        response_model: type[T],
        *,
        system: str | None = None,
    ) -> T:
        """Call the model and return a validated *response_model* instance.

        Args:
            prompt:         User-turn text.
            response_model: Pydantic model class the LLM must populate.
            system:         Optional system prompt injected before the user turn.

        Returns:
            A fully-validated instance of *response_model*.

        Raises:
            instructor.exceptions.InstructorRetryException: if the model fails
                to produce a valid response after all retries.
        """
        ...
