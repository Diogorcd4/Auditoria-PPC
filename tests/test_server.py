import json

import httpx
import pytest

import auditor.server as server_module
from auditor.pipeline import PipelineEvent


async def _fake_run_pipeline(config, llm, **kwargs):
    yield PipelineEvent(step="crawl", status="running")
    yield PipelineEvent(step="crawl", status="done", data={"domain": "example.pt", "pages": []}, duration_seconds=0.1)
    yield PipelineEvent(step="tracking", status="error", error="falhou de propósito")


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=server_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_get_demo_audit_returns_the_fixture(client):
    resp = await client.get("/api/demo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["demo"] is True
    assert "ads" in data


@pytest.mark.asyncio
async def test_static_index_html_is_served(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Auditor" in resp.text


@pytest.mark.asyncio
async def test_stream_audit_emits_sse_events_in_order(monkeypatch, client):
    monkeypatch.setattr(server_module, "run_pipeline", _fake_run_pipeline)
    monkeypatch.setattr(server_module, "_get_llm_client", lambda mock: object())

    events = []
    async with client.stream("GET", "/api/audit/stream", params={"url": "example.pt", "mock": "true"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))

    assert [e["step"] for e in events] == ["crawl", "crawl", "tracking"]
    assert events[0]["status"] == "running"
    assert events[1]["status"] == "done"
    assert events[1]["data"]["domain"] == "example.pt"
    assert events[2]["status"] == "error"
    assert events[2]["error"] == "falhou de propósito"


@pytest.mark.asyncio
async def test_stream_audit_passes_mock_flag_through_to_get_llm_client(monkeypatch, client):
    seen = {}

    def fake_get_llm_client(mock):
        seen["mock"] = mock
        return object()

    monkeypatch.setattr(server_module, "run_pipeline", _fake_run_pipeline)
    monkeypatch.setattr(server_module, "_get_llm_client", fake_get_llm_client)

    async with client.stream("GET", "/api/audit/stream", params={"url": "example.pt", "mock": "true"}) as response:
        async for _ in response.aiter_lines():
            pass

    assert seen["mock"] is True


def _sample_profile():
    fields = {name: {"value": "x", "origin": "site", "confidence": 0.8} for name in [
        "sector", "empresa", "publico", "descricao_publico", "acao", "geografia", "landing_page_url",
        "pagina_destino", "produto_ou_servico", "preco", "idade", "perfil", "nivel_poder_compra",
        "motivacao", "objecao", "comportamento_online", "objetivo_conversao", "fonte_dados_clientes",
        "fonte_dados_subscritores", "conteudo_a_promover", "objectivo_pos_trafego", "motivacao_clique", "canais",
    ]}
    fields["business_model"] = {"value": "leads", "origin": "inferido", "confidence": 0.8}
    fields["conteudo_forte"] = False
    return fields


def _sample_pages():
    return [{"id": "home", "url": "https://example.pt/", "type": "home", "title": "Home"}]


@pytest.mark.asyncio
async def test_regenerate_ads_calls_generate_all_ads_and_returns_counts(monkeypatch, client):
    captured = {}

    async def fake_generate_all_ads(llm, templates, profile, pages, *, model="", **kwargs):
        captured["templates"] = templates
        return {"02.1": {"headlines": [{"valid": True}, {"valid": False}], "descriptions": [{"valid": True}]}}

    monkeypatch.setattr(server_module, "generate_all_ads", fake_generate_all_ads)
    monkeypatch.setattr(server_module, "_get_llm_client", lambda mock: object())

    resp = await client.post(
        "/api/audit/regenerate-ads",
        json={"profile": _sample_profile(), "pages": _sample_pages(), "mock": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ads_valid_count"] == 2
    assert data["ads_total_count"] == 3
    assert captured["templates"] == ["02.1_search_leads.md", "02.2_pmax_leads.md", "02.5_meta_leads.md"]


@pytest.mark.asyncio
async def test_regenerate_ads_requires_profile_and_pages(client):
    resp = await client.post("/api/audit/regenerate-ads", json={"profile": None, "pages": None})
    assert resp.status_code == 400
