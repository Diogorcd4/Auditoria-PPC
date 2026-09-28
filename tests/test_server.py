import json
import shutil

import httpx
import pytest

import auditor.prompts as prompts_module
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


@pytest.fixture
def history_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "OUTPUT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def prompts_env(tmp_path, monkeypatch):
    tmp_prompts = tmp_path / "prompts"
    tmp_defaults = tmp_prompts / "defaults"
    tmp_defaults.mkdir(parents=True)
    for name in prompts_module.list_template_names():
        shutil.copy(prompts_module.PROMPTS_DIR / name, tmp_prompts / name)
        shutil.copy(prompts_module.DEFAULTS_DIR / name, tmp_defaults / name)
    monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_prompts)
    monkeypatch.setattr(prompts_module, "DEFAULTS_DIR", tmp_defaults)
    monkeypatch.setattr(server_module, "PROMPTS_DIR", tmp_prompts)
    return tmp_prompts


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    from auditor.appconfig import CONFIG_PATH as REAL_CONFIG_PATH
    from auditor.appconfig import load_config as real_load_config
    from auditor.appconfig import save_config as real_save_config

    tmp_config = tmp_path / "config.yaml"
    shutil.copy(REAL_CONFIG_PATH, tmp_config)

    monkeypatch.setattr(server_module, "load_config", lambda: real_load_config(tmp_config))
    monkeypatch.setattr(server_module, "save_config", lambda config: real_save_config(config, tmp_config))
    return tmp_config


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


# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_is_empty_list_when_nothing_was_ever_audited(client, history_env):
    resp = await client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_history_round_trip_via_pipeline_then_server(client, history_env):
    from auditor.pipeline import _update_history_index

    report = {"meta": {"domain": "example.pt", "audited_at": "2026-01-01T00:00:00Z", "communication_score": 70, "business_model": "leads"}}
    _update_history_index(history_env, "example.pt", "current", report)
    run_dir = history_env / "example.pt" / "current"
    run_dir.mkdir(parents=True)
    (run_dir / "audit.json").write_text(json.dumps(report), encoding="utf-8")

    resp = await client.get("/api/history")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["domain"] == "example.pt"
    assert entries[0]["audit_id"] == "current"

    resp2 = await client.get("/api/audits/example.pt/current")
    assert resp2.status_code == 200
    assert resp2.json()["meta"]["domain"] == "example.pt"

    resp3 = await client.get("/api/audits/example.pt/does-not-exist")
    assert resp3.status_code == 404

    resp4 = await client.delete("/api/audits/example.pt/current")
    assert resp4.status_code == 200
    assert not run_dir.exists()

    resp5 = await client.get("/api/history")
    assert resp5.json() == []


@pytest.mark.asyncio
async def test_delete_unknown_audit_returns_404(client, history_env):
    resp = await client.delete("/api/audits/nope.pt/current")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_prompts_returns_all_seven_templates_marked_as_default(client, prompts_env):
    resp = await client.get("/api/prompts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 7
    assert all(entry["is_default"] for entry in data.values())
    assert "[SECTOR]" in data["02.1_search_leads.md"]["content"]


@pytest.mark.asyncio
async def test_save_prompt_then_reset_round_trip(client, prompts_env):
    resp = await client.post("/api/prompts/02.1_search_leads.md", json={"content": "texto editado"})
    assert resp.status_code == 200
    assert resp.json()["is_default"] is False
    assert (prompts_env / "02.1_search_leads.md").read_text(encoding="utf-8") == "texto editado"

    resp2 = await client.get("/api/prompts")
    assert resp2.json()["02.1_search_leads.md"]["content"] == "texto editado"
    assert resp2.json()["02.1_search_leads.md"]["is_default"] is False

    resp3 = await client.post("/api/prompts/02.1_search_leads.md/reset")
    assert resp3.status_code == 200
    assert resp3.json()["is_default"] is True
    assert "[SECTOR]" in resp3.json()["content"]


@pytest.mark.asyncio
async def test_save_prompt_rejects_an_unknown_template_name(client, prompts_env):
    resp = await client.post("/api/prompts/does-not-exist.md", json={"content": "x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Definições
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings_returns_the_expected_keys(client, settings_env):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"app_name", "owner_services", "llm", "crawl", "tracking"}
    assert data["llm"]["backend"] == "ollama"


@pytest.mark.asyncio
async def test_update_settings_persists_to_disk(client, settings_env):
    resp = await client.post("/api/settings", json={"app_name": "Auditor PT", "crawl": {"max_pages": 10, "delay_seconds": 2, "respect_robots": False}})
    assert resp.status_code == 200
    assert resp.json()["app_name"] == "Auditor PT"

    resp2 = await client.get("/api/settings")
    assert resp2.json()["app_name"] == "Auditor PT"
    assert resp2.json()["crawl"]["max_pages"] == 10

    from auditor.appconfig import load_config

    saved = load_config(settings_env)
    assert saved["app_name"] == "Auditor PT"
    assert saved["crawl"]["max_pages"] == 10


@pytest.mark.asyncio
async def test_test_ollama_endpoint_reports_unreachable_when_nothing_is_listening(client, settings_env):
    resp = await client.get("/api/settings/test-ollama", params={"ollama_url": "http://127.0.0.1:1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reachable"] is False
    assert data["models"] == []
