from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx

from .base import LLMClient, LLMResponse


class OllamaClient(LLMClient):
    """Talks to a local Ollama instance (http://localhost:11434 by default). Free, no API key.

    Streams the response instead of waiting for one giant blocking POST, so a slow CPU-only
    machine generating token by token never trips a single global timeout: each streamed chunk
    gets its own timeout, and the very first chunk (which includes loading the model into
    memory, potentially slow) gets a longer allowance than the rest.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        num_ctx: int = 4096,
        chunk_timeout: float = 180.0,
        model_load_timeout: float = 300.0,
        keep_alive: str = "30m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.chunk_timeout = chunk_timeout
        self.model_load_timeout = model_load_timeout
        self.keep_alive = keep_alive

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
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
        }
        if json_schema:
            payload["format"] = json_schema

        start = time.monotonic()
        parts: list[str] = []
        # No blanket httpx timeout here - each chunk is timed out individually below, with the
        # first one (model load) getting a longer allowance than the rest.
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
            async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as resp:
                resp.raise_for_status()
                line_iter = resp.aiter_lines()
                first_chunk = True
                while True:
                    chunk_timeout = self.model_load_timeout if first_chunk else self.chunk_timeout
                    try:
                        line = await asyncio.wait_for(line_iter.__anext__(), timeout=chunk_timeout)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as exc:
                        phase = "a carregar o modelo" if first_chunk else "a gerar texto"
                        raise RuntimeError(f"O Ollama não respondeu dentro de {chunk_timeout:.0f}s ({phase}).") from exc
                    first_chunk = False
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    parts.append(data.get("response", ""))
                    if data.get("done"):
                        break

        return LLMResponse(content="".join(parts), model=model, backend="ollama", duration_seconds=time.monotonic() - start)

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
