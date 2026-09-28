from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from .base import LLMClient, LLMResponse


class OpenAICompatClient(LLMClient):
    """Talks to any OpenAI-compatible chat completions endpoint (LM Studio, Gemini, Groq, OpenRouter...)."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
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
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
        }
        if json_schema:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return LLMResponse(
            content=content,
            model=model,
            backend="openai_compatible",
            duration_seconds=time.monotonic() - start,
        )

    async def list_models(self) -> list[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return [m["id"] for m in data.get("data", [])]

    async def health_check(self) -> bool:
        try:
            await self.list_models()
            return True
        except httpx.HTTPError:
            return False
