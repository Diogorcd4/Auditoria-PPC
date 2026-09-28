from __future__ import annotations

import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from auditor.ads import generate_all_ads
from auditor.appconfig import build_llm_client, load_config, model_for_task
from auditor.crawler import PageData
from auditor.llm.mock import MockLLMClient
from auditor.pipeline import PipelineConfig, count_ads_assets, run_pipeline
from auditor.prompts import select_templates_for_profile

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
DEMO_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo_audit.json"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"

app = FastAPI(title="Auditor")


@app.get("/api/demo")
def get_demo_audit() -> JSONResponse:
    """Serve the fixture that powers the #/demo route. No LLM, no network, no Ollama needed."""
    if not DEMO_FIXTURE_PATH.exists():
        raise HTTPException(status_code=404, detail="Fixture de demonstração não encontrada.")
    data = json.loads(DEMO_FIXTURE_PATH.read_text(encoding="utf-8"))
    return JSONResponse(data)


def _get_llm_client(mock: bool):
    """Kept as a separate, monkeypatchable seam: tests replace this to inject a MockLLMClient
    preloaded with fixtures, without ever needing a real Ollama install to test the SSE wiring."""
    if mock:
        return MockLLMClient()
    return build_llm_client(load_config())


def _pipeline_config_from_app_config(url: str, max_pages: int, app_config: dict) -> PipelineConfig:
    return PipelineConfig(
        url=url,
        max_pages=max_pages,
        delay_seconds=app_config["crawl"]["delay_seconds"],
        respect_robots=app_config["crawl"]["respect_robots"],
        tracking_pages=app_config["tracking"]["pages"],
        owner_services=list(app_config.get("owner_services", [])),
        model_for_task={task: model_for_task(app_config, task) for task in ("analise", "keywords", "perfil", "anuncios")},
        output_dir=OUTPUT_DIR,
        cache_dir=CACHE_DIR,
    )


@app.get("/api/audit/stream")
async def stream_audit(request: Request, url: str, max_pages: int = 25, mock: bool = False) -> StreamingResponse:
    """SSE stream of PipelineEvent objects, one per pipeline step transition. The client
    (EventSource in app.js) stops listening simply by closing the connection - detected here
    via `request.is_disconnected()` so a cancelled audit doesn't keep running server-side."""
    app_config = load_config()
    llm = _get_llm_client(mock)
    pipeline_config = _pipeline_config_from_app_config(url, max_pages, app_config)

    async def event_source():
        async for event in run_pipeline(pipeline_config, llm):
            if await request.is_disconnected():
                break
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.post("/api/audit/regenerate-ads")
async def regenerate_ads(payload: dict = Body(...)) -> JSONResponse:
    """Re-run only the "anuncios" step, for the "Regenerar anúncios com este perfil" button:
    the client already holds `profile` and `pages` from the finished audit, so this never
    needs to re-crawl or re-run tracking/analysis - it goes straight to ad generation."""
    profile = payload.get("profile")
    pages_raw = payload.get("pages")
    if not profile or not pages_raw:
        raise HTTPException(status_code=400, detail="É preciso indicar 'profile' e 'pages'.")

    try:
        pages = [PageData.model_validate(p) for p in pages_raw]
    except Exception as exc:  # noqa: BLE001 - surfaced to the client as a 400, not a 500
        raise HTTPException(status_code=400, detail=f"'pages' inválido: {exc}") from None

    app_config = load_config()
    llm = _get_llm_client(bool(payload.get("mock", False)))
    templates = select_templates_for_profile(profile["business_model"]["value"], profile.get("conteudo_forte", False))
    model = model_for_task(app_config, "anuncios")

    ads = await generate_all_ads(llm, templates, profile, pages, model=model)
    valid, total = count_ads_assets(ads)
    return JSONResponse({"ads": ads, "ads_valid_count": valid, "ads_total_count": total})


# Routing is hash-based (#/demo, #/history...), so the server only ever needs to serve
# static assets; the browser path stays "/" and the fragment is handled in app.js.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
