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
