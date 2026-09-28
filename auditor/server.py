from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path
from typing import Callable

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from auditor.ads import generate_all_ads
from auditor.appconfig import GEMINI_BASE_URL, build_llm_client_for_task, load_config, model_for_task, save_config
from auditor.crawler import PageData
from auditor.llm.base import LLMClient
from auditor.llm.mock import MockLLMClient
from auditor.pipeline import (
    PipelineConfig,
    _format_error,
    count_ads_assets,
    delete_saved_audit,
    load_history,
    load_saved_audit,
    run_pipeline,
)
from auditor.prompts import PROMPTS_DIR, TEMPLATE_PLACEHOLDERS, list_template_names, load_template
from auditor.prompts import select_templates_for_profile

SSE_PING_INTERVAL = 15.0

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


def _get_llm_factory(mock: bool) -> Callable[[str], LLMClient]:
    """One client per task (secção D.12), cached for the lifetime of a single audit run so a
    rate-limited backend (Gemini gratuito) keeps its request-timing state across every call
    instead of resetting it on each step."""
    if mock:
        client = MockLLMClient()
        return lambda _task: client

    app_config = load_config()
    cache: dict[str, LLMClient] = {}

    def factory(task: str) -> LLMClient:
        if task not in cache:
            cache[task] = build_llm_client_for_task(app_config, task)
        return cache[task]

    return factory


def _pipeline_config_from_app_config(url: str, max_pages: int, app_config: dict, *, fast: bool = False) -> PipelineConfig:
    if fast:
        max_pages = min(max_pages, 5)
    return PipelineConfig(
        url=url,
        max_pages=max_pages,
        delay_seconds=app_config["crawl"]["delay_seconds"],
        respect_robots=app_config["crawl"]["respect_robots"],
        tracking_pages=app_config["tracking"]["pages"],
        max_page_chars=app_config["llm"].get("max_page_chars", 4000),
        owner_services=list(app_config.get("owner_services", [])),
        model_for_task={task: model_for_task(app_config, task) for task in ("analise", "keywords", "perfil", "anuncios")},
        output_dir=OUTPUT_DIR,
        cache_dir=CACHE_DIR,
        # A fixed slot per domain (rather than a fresh timestamp every time) is what makes
        # reloading the page mid-audit "just reconnect" instead of starting over: the resumed
        # run picks up from whichever steps already have a checkpoint (secção 5.5 do briefing).
        audit_id="current",
        resume=True,
    )


@app.get("/api/audit/stream")
async def stream_audit(request: Request, url: str, max_pages: int = 25, mock: bool = False, fast: bool = False) -> StreamingResponse:
    """SSE stream of PipelineEvent objects, one per pipeline step transition. The client
    (EventSource in app.js) stops listening simply by closing the connection - detected here
    via `request.is_disconnected()` so a cancelled audit doesn't keep running server-side.

    A ": ping" comment line is sent whenever nothing else has happened for
    SSE_PING_INTERVAL seconds - including while a single slow model call is in flight - so
    browsers/proxies never decide the connection is dead mid-step (secção C.8). Any exception
    that escapes run_pipeline itself (e.g. the browser failing to launch, before the per-step
    try/except even starts) is still turned into one last error event instead of just
    dropping the connection with no explanation (secção B.7).
    """
    app_config = load_config()
    llm_factory = _get_llm_factory(mock)
    pipeline_config = _pipeline_config_from_app_config(url, max_pages, app_config, fast=fast)

    async def event_source():
        agen = run_pipeline(pipeline_config, llm_factory).__aiter__()
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(agen.__anext__(), timeout=SSE_PING_INTERVAL)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                break
            except Exception as exc:  # noqa: BLE001 - never let the SSE stream just die silently
                traceback.print_exc()
                from auditor.pipeline import PipelineEvent

                error_event = PipelineEvent(step="pipeline", status="error", error=_format_error(exc))
                yield f"data: {error_event.model_dump_json()}\n\n"
                break
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    llm = MockLLMClient() if payload.get("mock", False) else build_llm_client_for_task(app_config, "anuncios")
    templates = select_templates_for_profile(profile["business_model"]["value"], profile.get("conteudo_forte", False))
    model = model_for_task(app_config, "anuncios")

    ads = await generate_all_ads(llm, templates, profile, pages, model=model)
    valid, total = count_ads_assets(ads)
    return JSONResponse({"ads": ads, "ads_valid_count": valid, "ads_total_count": total})


@app.get("/api/history")
def get_history() -> JSONResponse:
    return JSONResponse(load_history(OUTPUT_DIR))


@app.get("/api/audits/{domain}/{audit_id}")
def get_saved_audit(domain: str, audit_id: str) -> JSONResponse:
    data = load_saved_audit(OUTPUT_DIR, domain, audit_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")
    return JSONResponse(data)


@app.delete("/api/audits/{domain}/{audit_id}")
def remove_saved_audit(domain: str, audit_id: str) -> JSONResponse:
    deleted = delete_saved_audit(OUTPUT_DIR, domain, audit_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")
    return JSONResponse({"deleted": True})


SETTINGS_KEYS = {"app_name", "owner_services", "llm", "crawl", "tracking"}


@app.get("/api/settings")
def get_settings() -> JSONResponse:
    config = load_config()
    return JSONResponse({key: config[key] for key in SETTINGS_KEYS if key in config})


@app.post("/api/settings")
def update_settings(payload: dict = Body(...)) -> JSONResponse:
    config = load_config()
    for key in SETTINGS_KEYS:
        if key in payload:
            config[key] = payload[key]
    save_config(config)
    return JSONResponse({key: config[key] for key in SETTINGS_KEYS if key in config})


@app.get("/api/settings/test-ollama")
async def test_ollama(ollama_url: str = "") -> JSONResponse:
    from auditor.llm import OllamaClient

    base_url = ollama_url or load_config()["llm"]["ollama_url"]
    client = OllamaClient(base_url=base_url)
    reachable = await client.health_check()
    models = await client.list_models() if reachable else []
    return JSONResponse({"reachable": reachable, "models": models})


@app.get("/api/settings/test-openai-compatible")
async def test_openai_compatible(base_url: str = "") -> JSONResponse:
    """Used by the "Gemini gratuito" preset button in Definições: checks the key from .env
    against the given (or default Gemini) base_url, and suggests the first Flash-Lite model
    it finds (secção D.17). The API key itself never leaves the server."""
    from auditor.appconfig import get_api_key
    from auditor.llm import OpenAICompatClient

    url = base_url or GEMINI_BASE_URL
    api_key = get_api_key()
    if not api_key:
        return JSONResponse({"reachable": False, "models": [], "suggested_model": None, "detail": "AUDITOR_API_KEY não está definida no ficheiro .env."})

    client = OpenAICompatClient(base_url=url, api_key=api_key)
    try:
        models = await client.list_models()
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not a 500
        return JSONResponse({"reachable": False, "models": [], "suggested_model": None, "detail": _format_error(exc)})

    suggested = next((m for m in models if "flash-lite" in m.lower()), None) or next((m for m in models if "flash" in m.lower()), None)
    return JSONResponse({"reachable": True, "models": models, "suggested_model": suggested})


@app.get("/api/prompts")
def get_prompts() -> JSONResponse:
    data = {}
    for name in list_template_names():
        content = load_template(name)
        data[name] = {"content": content, "is_default": content == load_template(name, defaults=True)}
    return JSONResponse(data)


@app.post("/api/prompts/{name}")
def save_prompt(name: str, payload: dict = Body(...)) -> JSONResponse:
    if name not in TEMPLATE_PLACEHOLDERS:
        raise HTTPException(status_code=404, detail="Template desconhecido.")
    content = payload.get("content", "")
    (PROMPTS_DIR / name).write_text(content, encoding="utf-8")
    return JSONResponse({"content": content, "is_default": content == load_template(name, defaults=True)})


@app.post("/api/prompts/{name}/reset")
def reset_prompt(name: str) -> JSONResponse:
    if name not in TEMPLATE_PLACEHOLDERS:
        raise HTTPException(status_code=404, detail="Template desconhecido.")
    default_content = load_template(name, defaults=True)
    (PROMPTS_DIR / name).write_text(default_content, encoding="utf-8")
    return JSONResponse({"content": default_content, "is_default": True})


# Routing is hash-based (#/demo, #/history...), so the server only ever needs to serve
# static assets; the browser path stays "/" and the fragment is handled in app.js.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
