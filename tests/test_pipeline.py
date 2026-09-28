import json

import httpx
import pytest

from auditor.llm.mock import MockLLMClient
from auditor.pipeline import PipelineConfig, STEPS, run_pipeline

FIXTURES = {
    "analise": "analysis_page.json",
    "sintese": "site_synthesis.json",
    "keywords": "keywords_synthesis.json",
    "perfil": "site_profile.json",
}


def _write_site(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        """<!doctype html><html><head><meta charset="utf-8"><title>Clínica Teste</title>
        <meta name="description" content="Uma clínica de teste"></head>
        <body>
        <nav><a href="/contacto/">Contacto</a></nav>
        <h1>Bem-vindo à Clínica Teste</h1>
        <p>Marque a sua consulta de avaliação gratuita.</p>
        <button class="btn">Marcar consulta</button>
        <footer>Rodapé</footer>
        </body></html>""",
        encoding="utf-8",
    )
    (root / "contacto").mkdir(exist_ok=True)
    (root / "contacto" / "index.html").write_text(
        "<html><head><title>Contacto</title></head><body><h1>Fale connosco</h1></body></html>",
        encoding="utf-8",
    )


def _keyword_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q")
        return httpx.Response(200, json=[query, []])

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_run_pipeline_runs_every_step_and_produces_a_final_report(make_site_server, tmp_path, browser):
    _write_site(tmp_path / "site")
    base_url = make_site_server(tmp_path / "site")
    llm = MockLLMClient(fixtures=FIXTURES)

    config = PipelineConfig(
        url=base_url,
        max_pages=5,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / ".cache",
    )

    events = []
    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for event in run_pipeline(config, llm, browser=browser, http_client=http_client):
            events.append(event)

    steps_seen = [e.step for e in events if e.status == "done"]
    assert steps_seen == STEPS
    assert all(e.status != "error" for e in events)

    report_event = next(e for e in events if e.step == "relatorio" and e.status == "done")
    report = report_event.data
    assert report["meta"]["domain"]
    assert report["meta"]["demo"] is False
    assert report["meta"]["pages_analyzed"] == len(report["pages"])
    assert set(report["business_categories"].keys()) == {
        "problemas", "caracteristicas", "motivos_para_comprar", "objeccoes_e_receios", "desejos", "crencas_e_mentalidade", "oportunidades",
    }
    assert report["profile"]["business_model"]["value"] in ("leads", "ecommerce")
    assert report["opportunities"]  # a fresh site with no tracking at all triggers several rules


@pytest.mark.asyncio
async def test_run_pipeline_writes_checkpoints_and_the_final_audit_json(make_site_server, tmp_path, browser):
    _write_site(tmp_path / "site")
    base_url = make_site_server(tmp_path / "site")
    llm = MockLLMClient(fixtures=FIXTURES)
    output_dir = tmp_path / "output"

    config = PipelineConfig(
        url=base_url,
        max_pages=3,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=output_dir,
        cache_dir=tmp_path / ".cache",
    )

    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for _ in run_pipeline(config, llm, browser=browser, http_client=http_client):
            pass

    domain_dirs = [p for p in output_dir.iterdir() if p.is_dir()]
    assert len(domain_dirs) == 1
    run_dirs = list(domain_dirs[0].iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    checkpoint_files = {p.stem for p in (run_dir / "checkpoints").glob("*.json")}
    assert checkpoint_files == set(STEPS)
    assert (run_dir / "audit.json").exists()

    index = json.loads((output_dir / "index.json").read_text(encoding="utf-8"))
    assert len(index) == 1
    assert index[0]["domain"]


@pytest.mark.asyncio
async def test_run_pipeline_resume_skips_steps_with_an_existing_checkpoint(make_site_server, tmp_path, browser):
    _write_site(tmp_path / "site")
    base_url = make_site_server(tmp_path / "site")
    llm = MockLLMClient(fixtures=FIXTURES)
    output_dir = tmp_path / "output"
    cache_dir = tmp_path / ".cache"

    audit_id = "fixed-run-id"
    config = PipelineConfig(
        url=base_url,
        max_pages=3,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=output_dir,
        cache_dir=cache_dir,
        only=["crawl", "tracking"],
        audit_id=audit_id,
    )
    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for _ in run_pipeline(config, llm, browser=browser, http_client=http_client):
            pass

    resumed_config = PipelineConfig(
        url=base_url,
        max_pages=3,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=output_dir,
        cache_dir=cache_dir,
        only=["crawl", "tracking"],
        audit_id=audit_id,
        resume=True,
    )
    events = []
    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for event in run_pipeline(resumed_config, llm, browser=browser, http_client=http_client):
            events.append(event)

    # a resumed step never emits "running" - it is loaded straight from its checkpoint
    assert not any(e.status == "running" for e in events)
    assert [e.step for e in events] == ["crawl", "tracking"]


@pytest.mark.asyncio
async def test_run_pipeline_reports_a_failing_step_as_an_error_event_without_stopping(make_site_server, tmp_path, browser):
    _write_site(tmp_path / "site")
    base_url = make_site_server(tmp_path / "site")
    # No "perfil" fixture registered -> MockLLMClient falls back to default_response.json,
    # which doesn't exist, so generate_json will fail to validate it as a SiteProfile.
    llm = MockLLMClient(fixtures={"analise": "analysis_page.json", "sintese": "site_synthesis.json", "keywords": "keywords_synthesis.json"})

    config = PipelineConfig(
        url=base_url,
        max_pages=3,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / ".cache",
    )

    events = []
    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for event in run_pipeline(config, llm, browser=browser, http_client=http_client):
            events.append(event)

    perfil_events = [e for e in events if e.step == "perfil"]
    assert any(e.status == "error" for e in perfil_events)
    # the pipeline still reaches the end and reports on "anuncios"/"relatorio", even though
    # "perfil" failed - a broken step must never take down the rest of the audit
    assert any(e.step == "anuncios" for e in events)
