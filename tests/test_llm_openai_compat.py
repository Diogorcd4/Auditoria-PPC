"""Testes para o cliente OpenAICompatClient (backend usado pelo Gemini gratuito): erros HTTP
devem transformar-se em mensagens PT-PT claras (nunca um erro em bruto/vazio), e o limite de
pedidos por minuto deve esperar em vez de simplesmente falhar."""

import httpx
import pytest

from auditor.llm.openai_compat import (
    OpenAICompatAuthError,
    OpenAICompatClient,
    OpenAICompatDailyLimitError,
    OpenAICompatModelNotFoundError,
)


def _patch_transport(monkeypatch, handler):
    """OpenAICompatClient cria o seu próprio httpx.AsyncClient a cada tentativa - para o testar
    sem rede, forçamos esse construtor a usar sempre o nosso MockTransport."""
    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


@pytest.mark.asyncio
async def test_401_becomes_an_auth_error_naming_the_env_file(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(401, json={"error": "invalid key"}))
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="bad-key")

    with pytest.raises(OpenAICompatAuthError) as excinfo:
        await client.generate(system="s", prompt="p", model="gemini-2.0-flash-lite")
    assert ".env" in str(excinfo.value)


@pytest.mark.asyncio
async def test_404_becomes_a_model_not_found_error_naming_the_model(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(404, json={"error": "not found"}))
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k")

    with pytest.raises(OpenAICompatModelNotFoundError) as excinfo:
        await client.generate(system="s", prompt="p", model="modelo-inexistente")
    assert "modelo-inexistente" in str(excinfo.value)


@pytest.mark.asyncio
async def test_429_with_daily_quota_wording_becomes_a_daily_limit_error(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(429, text="Daily quota exceeded for this API key"))
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k", max_retries=1)

    with pytest.raises(OpenAICompatDailyLimitError):
        await client.generate(system="s", prompt="p", model="m")


@pytest.mark.asyncio
async def test_429_without_daily_quota_wording_retries_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, text="rate limited, try later", headers={"Retry-After": "0"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _patch_transport(monkeypatch, handler)
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k", max_retries=2)

    result = await client.generate(system="s", prompt="p", model="m")
    assert result.content == "ok"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_respect_rate_limit_waits_and_reports_via_on_wait(monkeypatch):
    waits = []
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("auditor.llm.openai_compat.asyncio.sleep", fake_sleep)
    _patch_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))

    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k", requests_per_minute=1, on_wait=lambda s: waits.append(s))

    await client.generate(system="s", prompt="p1", model="m")
    await client.generate(system="s", prompt="p2", model="m")

    assert len(waits) == 1
    assert waits[0] > 0
    assert slept == waits


@pytest.mark.asyncio
async def test_daily_limit_raises_before_making_a_request_once_exhausted(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k", requests_per_minute=100, daily_limit=1)

    await client.generate(system="s", prompt="p1", model="m")
    with pytest.raises(OpenAICompatDailyLimitError):
        await client.generate(system="s", prompt="p2", model="m")
