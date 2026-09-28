from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

import httpx

from .base import LLMClient, LLMResponse


class OpenAICompatAuthError(RuntimeError):
    """401/403 - a chave de API está em falta ou é inválida."""


class OpenAICompatModelNotFoundError(RuntimeError):
    """404 - o modelo pedido não existe neste backend."""


class OpenAICompatDailyLimitError(RuntimeError):
    """429 cujo corpo indica que a quota diária (não a de minuto) foi esgotada."""


class OpenAICompatClient(LLMClient):
    """Talks to any OpenAI-compatible chat completions endpoint (Gemini gratuito, LM Studio,
    Groq, OpenRouter...). Respects a free-tier requests-per-minute budget by queueing instead
    of failing, retries 429s with exponential backoff / Retry-After, and turns opaque HTTP
    errors into PT-PT messages a person can actually act on.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 120.0,
        requests_per_minute: int = 8,
        daily_limit: int = 450,
        max_retries: int = 5,
        on_wait: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.requests_per_minute = max(1, requests_per_minute)
        self.daily_limit = daily_limit
        self.max_retries = max_retries
        self.on_wait = on_wait
        self._request_times: list[float] = []
        self.request_count = 0
        self._prefers_json_object = False  # flips to True once json_schema is seen unsupported

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def _respect_rate_limit(self) -> None:
        now = time.monotonic()
        self._request_times = [t for t in self._request_times if now - t < 60]
        if len(self._request_times) >= self.requests_per_minute:
            wait_seconds = 60 - (now - self._request_times[0])
            if wait_seconds > 0:
                if self.on_wait:
                    self.on_wait(wait_seconds)
                await asyncio.sleep(wait_seconds)
        self._request_times.append(time.monotonic())

        if self.daily_limit and self.request_count >= self.daily_limit:
            raise OpenAICompatDailyLimitError(
                f"Já foram feitos {self.request_count} pedidos nesta sessão, ao limite diário "
                f"configurado ({self.daily_limit}). Tente novamente amanhã, mude de modelo/backend "
                "nas Definições, ou aumente 'llm.daily_limit' em config.yaml."
            )

    def _payload(self, *, system: str, prompt: str, model: str, temperature: float, json_schema: Optional[dict]) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
        }
        if json_schema:
            if self._prefers_json_object:
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "resposta", "schema": json_schema, "strict": True},
                }
        return payload

    @staticmethod
    def _is_daily_quota_error(body_text: str) -> bool:
        lower = body_text.lower()
        return "quota" in lower and ("day" in lower or "daily" in lower or "diári" in lower)

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        model: str = "",
        json_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        start = time.monotonic()
        headers = self._headers()
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            await self._respect_rate_limit()
            payload = self._payload(system=system, prompt=prompt, model=model, temperature=temperature, json_schema=json_schema)

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"O pedido ao modelo '{model}' excedeu o tempo limite de {self.timeout:.0f}s "
                        f"(tentativa {attempt + 1} de {self.max_retries + 1})."
                    ) from exc
                continue

            self.request_count += 1

            if resp.status_code in (401, 403):
                raise OpenAICompatAuthError(
                    "Chave de API em falta ou inválida para este backend. Verifique AUDITOR_API_KEY "
                    "no ficheiro .env (nunca em config.yaml)."
                )
            if resp.status_code == 404:
                raise OpenAICompatModelNotFoundError(
                    f"O modelo '{model}' não foi encontrado neste backend. Confirme o nome exacto "
                    "nas Definições (o menu é preenchido a partir dos modelos que a API devolve)."
                )
            if resp.status_code == 400 and json_schema and not self._prefers_json_object:
                # Some OpenAI-compatible endpoints don't support response_format=json_schema yet.
                body_text = resp.text
                if "response_format" in body_text.lower() or "json_schema" in body_text.lower():
                    self._prefers_json_object = True
                    continue
            if resp.status_code == 429:
                body_text = resp.text
                if self._is_daily_quota_error(body_text):
                    raise OpenAICompatDailyLimitError(
                        "O limite diário gratuito desta API foi esgotado (resposta 429 com quota "
                        "diária). Tente novamente amanhã ou mude de modelo/backend nas Definições."
                    )
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Limite de pedidos por minuto excedido repetidamente (429) após {self.max_retries + 1} tentativas.")
                retry_after = resp.headers.get("Retry-After")
                wait_seconds = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(2**attempt, 60)
                if self.on_wait:
                    self.on_wait(wait_seconds)
                await asyncio.sleep(wait_seconds)
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise RuntimeError(f"O backend respondeu com o estado {resp.status_code}: {resp.text[:300]}") from exc
                continue

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return LLMResponse(content=content, model=model, backend="openai_compatible", duration_seconds=time.monotonic() - start)

        raise RuntimeError(f"Falha a obter resposta do backend após {self.max_retries + 1} tentativas: {last_exc}")

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        return [m["id"] for m in data.get("data", [])]

    async def health_check(self) -> bool:
        try:
            await self.list_models()
            return True
        except httpx.HTTPError:
            return False
