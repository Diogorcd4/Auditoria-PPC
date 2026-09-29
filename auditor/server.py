from __future__ import annotations

import asyncio
import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auditor.ads import generate_all_ads
from auditor.appconfig import GEMINI_BASE_URL, build_llm_client_for_task, load_config, model_for_task, save_config
from auditor.crawler import PageData
from auditor.llm.base import LLMClient
from auditor.llm.mock import MockLLMClient
from auditor.pipeline import (
    PipelineConfig,
    PipelineEvent,
    _format_error,
    _require_model,
    count_ads_assets,
    delete_saved_audit,
    domain_of,
    load_history,
    load_saved_audit,
    run_pipeline,
)
from auditor.prompts import PROMPTS_DIR, TEMPLATE_PLACEHOLDERS, list_template_names, load_template
from auditor.prompts import select_templates_for_profile

SSE_PING_INTERVAL = 15.0
JOB_POLL_INTERVAL = 0.5

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
DEMO_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo_audit.json"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"

app = FastAPI(title="Auditor")


# ---------------------------------------------------------------------------
# Auditorias em segundo plano (secção B do pedido de correcção)
#
# Uma auditoria corre numa tarefa asyncio própria, guardada em AUDIT_JOBS por domínio -
# nunca ligada ao ciclo de vida de uma ligação HTTP. Fechar o separador, recarregar a
# página, ou uma ligação SSE cair e voltar a ligar-se, NUNCA cancela nem reinicia o job;
# só o cancelamento explícito (endpoint /cancel) o faz. Cada evento fica também guardado em
# `events`, o que permite tanto reconstruir o estado de uma auditoria já terminada como
# retomar uma ligação SSE a meio (via Last-Event-ID) sem perder nem repetir eventos.
# ---------------------------------------------------------------------------


@dataclass
class AuditJob:
    id: str
    url: str
    mock: bool = False
    fast: bool = False
    max_pages: int = 25
    status: str = "running"  # running | done | error | cancelled
    events: list[dict] = field(default_factory=list)
    task: Optional["asyncio.Task"] = None


AUDIT_JOBS: dict[str, AuditJob] = {}


class StartAuditRequest(BaseModel):
    url: str
    max_pages: int = 25
    mock: bool = False
    fast: bool = False


async def _run_audit_job(job: AuditJob) -> None:
    app_config = load_config()
    llm_factory = _get_llm_factory(job.mock)
    pipeline_config = _pipeline_config_from_app_config(job.url, job.max_pages, app_config, fast=job.fast)
    print(f"[audits] job '{job.id}' iniciado (url={job.url})")
    try:
        async for event in run_pipeline(pipeline_config, llm_factory):
            job.events.append(event.model_dump())
    except asyncio.CancelledError:
        job.status = "cancelled"
        print(f"[audits] job '{job.id}' cancelado")
        return
    except Exception as exc:  # noqa: BLE001 - never let a background job vanish silently
        traceback.print_exc()
        job.events.append(PipelineEvent(step="pipeline", status="error", error=_format_error(exc)).model_dump())
        job.status = "error"
        print(f"[audits] job '{job.id}' terminou com erro: {job.status}")
        return
    job.status = "done"
    print(f"[audits] job '{job.id}' concluído")


def _get_or_reuse_audit_job(payload: StartAuditRequest) -> tuple[AuditJob, bool]:
    job_id = domain_of(payload.url)
    existing = AUDIT_JOBS.get(job_id)
    if existing is not None and existing.status == "running":
        print(f"[audits] pedido para '{payload.url}' reutiliza o job '{job_id}' já em curso")
        return existing, True

    job = AuditJob(id=job_id, url=payload.url, mock=payload.mock, fast=payload.fast, max_pages=payload.max_pages)
    AUDIT_JOBS[job_id] = job
    job.task = asyncio.create_task(_run_audit_job(job))
    return job, False


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
        fast=fast,
        output_dir=OUTPUT_DIR,
        cache_dir=CACHE_DIR,
        # A fixed slot per domain (rather than a fresh timestamp every time) is what makes
        # reloading the page mid-audit "just reconnect" instead of starting over: the resumed
        # run picks up from whichever steps already have a checkpoint (secção 5.5 do briefing).
        audit_id="current",
        resume=True,
    )


@app.post("/api/audits")
async def start_audit(payload: StartAuditRequest) -> JSONResponse:
    """Cria um job de auditoria em segundo plano, ou devolve o id do que já estiver em curso
    para o mesmo domínio (secção B.2) - nunca corre dois jobs em paralelo para o mesmo site."""
    job, reused = _get_or_reuse_audit_job(payload)
    return JSONResponse({"id": job.id, "reused": reused})


@app.get("/api/audits/{job_id}/events")
async def stream_audit_job_events(job_id: str, request: Request) -> StreamingResponse:
    """SSE do job `job_id`. Nunca cancela nem reinicia o job ao desligar-se (secção B.1): só
    lê o que já está em `job.events` e vai dormindo até haver mais. O cabeçalho Last-Event-ID
    (enviado automaticamente pelo EventSource do browser ao reconectar) faz recomeçar exactamente
    a seguir ao último evento recebido, em vez de repetir tudo desde o início (secção B.1)."""
    job = AUDIT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")

    last_event_id = request.headers.get("last-event-id")
    start_index = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else 0
    print(f"[audits] ligação SSE aberta para o job '{job_id}' (a partir do evento {start_index})")

    async def event_source():
        close_reason = "o cliente desligou-se"
        index = start_index
        last_activity = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    return
                if index < len(job.events):
                    yield f"id: {index}\ndata: {json.dumps(job.events[index], ensure_ascii=False)}\n\n"
                    index += 1
                    last_activity = time.monotonic()
                    continue
                if job.status != "running":
                    close_reason = f"o job terminou (estado: {job.status})"
                    return
                if time.monotonic() - last_activity >= SSE_PING_INTERVAL:
                    yield ": ping\n\n"
                    last_activity = time.monotonic()
                else:
                    await asyncio.sleep(JOB_POLL_INTERVAL)
        finally:
            print(f"[audits] ligação SSE fechada para o job '{job_id}' ({close_reason})")

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/audits/{job_id}")
def get_audit_job(job_id: str) -> JSONResponse:
    """Estado completo do job (secção B.3): usado para reconstruir o ecrã de uma auditoria em
    curso ou já terminada, sem depender de nenhum evento SSE em tempo real."""
    job = AUDIT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")
    return JSONResponse({"id": job.id, "url": job.url, "status": job.status, "events": job.events})


@app.post("/api/audits/{job_id}/cancel")
async def cancel_audit_job(job_id: str) -> JSONResponse:
    """Único caminho que pára um job (secção B.1): fechar o separador, recarregar a página ou
    a ligação SSE cair nunca chega aqui - só o botão "Cancelar" chama este endpoint."""
    job = AUDIT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")
    if job.task is not None and not job.task.done():
        job.task.cancel()
    job.status = "cancelled"
    print(f"[audits] job '{job_id}' cancelado pelo utilizador")
    return JSONResponse({"cancelled": True})


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
    try:
        _require_model(llm, "anuncios", model)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

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
    # Só as chaves de Definições vão para config.local.yaml - nunca "validation" (que não é
    # editável aqui), para uma actualização futura das regras de validação nunca ficar
    # bloqueada por uma cópia antiga guardada por engano no ficheiro local do utilizador.
    save_config({key: config[key] for key in SETTINGS_KEYS if key in config})
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
