from __future__ import annotations

import abc
from typing import Any, Optional

from pydantic import BaseModel


class LLMResponse(BaseModel):
    content: str
    model: str
    backend: str
    duration_seconds: float = 0.0


class LLMClient(abc.ABC):
    """Common interface every backend (Ollama, OpenAI-compatible, mock) implements."""

    @abc.abstractmethod
    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        model: str = "",
        json_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Run one completion. `json_schema`, when given, asks the backend for structured JSON output."""

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """List the models currently available on this backend."""

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Return whether the backend is reachable right now."""
