from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from .base import LLMClient, LLMResponse


class OllamaClient(LLMClient):
    """Talks to a local Ollama instance (http://localhost:11434 by default). Free, no API key."""

    def __init__(self, base_url: str = "http://localhost:11434", num_ctx: int = 8192, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        model: str = "",
        json_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
        }
        if json_schema:
            payload["format"] = json_schema
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return LLMResponse(
            content=data.get("response", ""),
            model=model,
            backend="ollama",
            duration_seconds=time.monotonic() - start,
        )

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        return [m["name"] for m in data.get("models", [])]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
