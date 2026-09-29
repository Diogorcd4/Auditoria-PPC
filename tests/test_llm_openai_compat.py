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


_ORIGINAL_ASYNC_CLIENT_INIT = httpx.AsyncClient.__init__


def _patch_transport(monkeypatch, handler):
    """OpenAICompatClient cria o seu próprio httpx.AsyncClient a cada tentativa - para o testar
    sem rede, forçamos esse construtor a usar sempre o nosso MockTransport. Parte sempre do
    __init__ original (nunca do que já possa estar remendado por uma chamada anterior nesta
    mesma função de teste), para chamadas repetidas dentro do mesmo teste nunca acumularem
    remendos uns sobre os outros."""
    transport = httpx.MockTransport(handler)

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        _ORIGINAL_ASYNC_CLIENT_INIT(self, *args, **kwargs)

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


@pytest.mark.asyncio
async def test_empty_model_raises_immediately_without_ever_calling_the_api(monkeypatch):
    """Reproduz a causa-raiz do bug relatado: um modelo vazio nunca deve chegar a um pedido
    HTTP (que a API rejeitaria sempre com 400 'model is not specified') - falha logo, em
    Python, antes de qualquer chamada de rede (secção 1/2 do pedido de correcção)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não devia ter sido feito nenhum pedido HTTP com o modelo vazio")

    _patch_transport(monkeypatch, handler)
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k")

    with pytest.raises(ValueError):
        await client.generate(system="s", prompt="p", model="")


@pytest.mark.asyncio
async def test_models_prefix_is_stripped_from_the_payload_sent_to_the_api(monkeypatch):
    """Aceita o nome do modelo com ou sem o prefixo "models/" (secção 3) - a API de
    list_models devolve-o com o prefixo, mas o endpoint de chat completions só aceita o nome
    puro; o payload enviado nunca deve conter "models/"."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _patch_transport(monkeypatch, handler)
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k")

    result = await client.generate(system="s", prompt="p", model="models/gemini-flash-lite-latest")
    assert result.model == "gemini-flash-lite-latest"
    import json as _json

    assert _json.loads(captured["body"])["model"] == "gemini-flash-lite-latest"


@pytest.mark.asyncio
async def test_400_is_never_retried_and_does_not_count_towards_the_per_minute_quota(monkeypatch):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, text="model is not specified")

    _patch_transport(monkeypatch, handler)
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k", requests_per_minute=8, max_retries=5)

    with pytest.raises(RuntimeError) as excinfo:
        await client.generate(system="s", prompt="p", model="m")

    assert calls["count"] == 1  # nunca repetido
    assert "não repetido" in str(excinfo.value)
    assert client._request_times == []  # não consumiu a janela de pedidos por minuto


@pytest.mark.asyncio
async def test_401_403_and_404_do_not_count_towards_the_per_minute_quota(monkeypatch):
    for status, exc_type in ((401, OpenAICompatAuthError), (403, OpenAICompatAuthError), (404, OpenAICompatModelNotFoundError)):
        _patch_transport(monkeypatch, lambda request, status=status: httpx.Response(status, text="erro"))
        client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k")

        with pytest.raises(exc_type):
            await client.generate(system="s", prompt="p", model="m")

        assert client._request_times == []


@pytest.mark.asyncio
async def test_5xx_is_retried_with_exponential_backoff(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("auditor.llm.openai_compat.asyncio.sleep", fake_sleep)

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, text="service unavailable")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _patch_transport(monkeypatch, handler)
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k", max_retries=3)

    result = await client.generate(system="s", prompt="p", model="m")
    assert result.content == "ok"
    assert calls["count"] == 3
    assert len(slept) == 2  # recuo antes da 2ª e da 3ª tentativa
