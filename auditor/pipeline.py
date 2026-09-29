from __future__ import annotations

import asyncio
import json
import time
import traceback
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Union
from urllib.parse import urlparse

import httpx
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel

from auditor.ads import MAX_CORRECTION_ROUNDS, generate_all_ads
from auditor.analysis import PageCommunication, compute_communication_score, synthesize_site
from auditor.analysis import analyze_page as _analyze_page
from auditor.browser import launch_browser
from auditor.crawler import PageData, crawl_site
from auditor.keywords import build_keywords
from auditor.llm.base import LLMClient
from auditor.llm.openai_compat import OpenAICompatClient
from auditor.opportunities import derive_opportunities
from auditor.profile import extract_profile
from auditor.prompts import select_templates_for_profile
from auditor.tracking import detect_tracking, empty_report, merge_reports

STEPS = ["crawl", "tracking", "comunicacao", "sintese", "perfil", "termos", "anuncios", "relatorio"]

DEFAULT_OWNER_SERVICES = ["Google Ads", "Meta Ads", "Microsoft Advertising", "Tracking e Analytics", "Copy e conversão"]

TRACKING_PAGE_TYPE_PRIORITY = ["landing", "servico", "produto", "contacto", "precos"]

LLMFactory = Callable[[str], LLMClient]

# str(exc) is empty for several common network exceptions (ReadTimeout(), ConnectError()...);
# this fills in a PT-PT explanation instead of ever showing a bare "—" with no reason
# (secção A.1 do briefing).
_ERROR_CLASS_EXPLANATIONS = {
    "ReadTimeout": "o pedido ao modelo demorou demasiado tempo e foi interrompido (timeout de leitura)",
    "ConnectTimeout": "não foi possível ligar ao serviço a tempo (timeout de ligação)",
    "ConnectError": "não foi possível estabelecer ligação ao serviço - verifique se está a correr e acessível",
    "PoolTimeout": "não havia nenhuma ligação livre disponível a tempo",
    "TimeoutError": "a operação demorou demasiado tempo e foi interrompida",
    "CancelledError": "a operação foi cancelada",
    "RemoteProtocolError": "o servidor fechou a ligação de forma inesperada a meio da resposta",
}


def _format_error(exc: BaseException) -> str:
    """Never show a bare, empty error to the user (secção A.1): if str(exc) is empty, name
    the exception class and add a short PT-PT explanation of what that usually means."""
    message = str(exc).strip()
    if message:
        return message
    class_name = type(exc).__name__
    explanation = _ERROR_CLASS_EXPLANATIONS.get(class_name, "ocorreu um erro sem mensagem detalhada")
    return f"{class_name}: {explanation}"


class DependencyNotMetError(RuntimeError):
    """A step's prerequisite step didn't produce usable output (secção B.5)."""


def _require_model(llm_client: LLMClient, task: str, model: str) -> None:
    """O backend openai_compatible (ex.: Gemini gratuito) exige sempre um nome de modelo
    explícito - ao contrário do Ollama, que pode ter um modelo por defeito no próprio
    servidor local. Falhar aqui, antes de qualquer chamada à API, poupa uma tentativa que a
    API ia sempre recusar com 400 "model is not specified" (secção 2 do pedido de correcção)."""
    if isinstance(llm_client, OpenAICompatClient) and not model:
        raise RuntimeError(
            f"Falta o modelo para a tarefa '{task}' no config.yaml/config.local.yaml (o backend "
            "openai_compatible exige um modelo explícito, ex.: gemini-flash-lite-latest)."
        )


class PipelineEvent(BaseModel):
    step: str
    status: str  # running | progress | done | error
    data: Optional[dict] = None
    error: Optional[str] = None
    message: Optional[str] = None
    duration_seconds: float = 0.0


@dataclass
class PipelineConfig:
    url: str
    max_pages: int = 25
    delay_seconds: float = 1.5
    respect_robots: bool = True
    tracking_pages: int = 4
    keyword_delay_seconds: float = 0.3
    keyword_use_alphabet: bool = True
    keyword_overall_timeout: float = 120.0
    max_correction_rounds: int = MAX_CORRECTION_ROUNDS
    max_page_chars: int = 4000
    owner_services: list[str] = field(default_factory=lambda: list(DEFAULT_OWNER_SERVICES))
    model_for_task: dict[str, str] = field(default_factory=dict)
    fast: bool = False
    output_dir: Path = Path("output")
    cache_dir: Path = Path(".cache")
    only: Optional[list[str]] = None
    resume: bool = False
    audit_id: Optional[str] = None


def domain_of(url: str) -> str:
    if "://" not in url:
        url = f"https://{url}"
    return urlparse(url).netloc


def new_audit_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_llm_factory(llm: Union[LLMClient, LLMFactory]) -> LLMFactory:
    if isinstance(llm, LLMClient):
        return lambda _task: llm
    return llm  # already a (task) -> LLMClient callable


class Checkpoints:
    """One JSON file per completed step, under output/<domain>/<audit_id>/checkpoints/. This
    is what makes --resume possible: a step whose checkpoint already exists is loaded from
    disk instead of recomputed (and, since an LLM call can be slow, never re-run by accident)."""

    def __init__(self, output_dir: Path | str, domain: str, audit_id: str) -> None:
        self.run_dir = Path(output_dir) / domain / audit_id
        self.dir = self.run_dir / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, step: str) -> Path:
        return self.dir / f"{step}.json"

    def load(self, step: str) -> Optional[dict]:
        p = self.path(step)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def save(self, step: str, data: dict) -> None:
        self.path(step).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pages_for_tracking(pages: list[PageData], n: int) -> list[PageData]:
    home = next((p for p in pages if p.type == "home"), None)
    rest = [p for p in pages if p is not home]
    rest.sort(key=lambda p: TRACKING_PAGE_TYPE_PRIORITY.index(p.type) if p.type in TRACKING_PAGE_TYPE_PRIORITY else len(TRACKING_PAGE_TYPE_PRIORITY))
    ordered = ([home] if home else []) + rest
    return ordered[: max(n, 1)]


def count_ads_assets(ads: dict[str, Any]) -> tuple[int, int]:
    valid = 0
    total = 0
    for block in ads.values():
        for group_key in ("headlines", "long_headlines", "descriptions", "primary_text"):
            for item in block.get(group_key, []):
                total += 1
                valid += int(item.get("valid", False))
        for sitelink in block.get("sitelinks", []):
            total += 1
            valid += int(sitelink.get("text", {}).get("valid", False))
            for desc in sitelink.get("descriptions", []):
                total += 1
                valid += int(desc.get("valid", False))
    return valid, total


def _tracking_platforms_detected(tracking: dict) -> int:
    core = sum(1 for key in ("ga4", "gtm", "meta_pixel", "google_ads", "microsoft_uet") if tracking[key]["detected"])
    return core + (1 if tracking["consent_mode"]["detected"] else 0)


def _assemble_report(context: dict[str, Any], config: PipelineConfig, duration_seconds: float) -> dict:
    tracking = context["tracking"]
    comunicacao = context["comunicacao"]
    sintese = context["sintese"]
    termos = context["termos"]
    perfil = context["perfil"]
    anuncios = context.get("anuncios", {"ads": {}})
    pages = context["crawl"]["pages"]

    site_synthesis = sintese["site_synthesis"]
    opportunities = derive_opportunities(tracking, site_synthesis["global_gaps"], config.owner_services)
    ads = anuncios.get("ads", {})
    ads_valid_count, ads_total_count = count_ads_assets(ads)

    url = config.url if "://" in config.url else f"https://{config.url}"
    meta = {
        "domain": context["domain"],
        "url": url,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_seconds),
        "demo": False,
        "business_model": perfil["business_model"]["value"],
        "communication_score": sintese["communication_score"],
        "pages_analyzed": len(pages),
        "pages_analyzed_ok": comunicacao.get("pages_ok", len(comunicacao.get("pages", []))),
        "tracking_platforms_detected": _tracking_platforms_detected(tracking),
        "tracking_platforms_total": 6,
        "opportunities_count": len(opportunities),
        "ads_valid_count": ads_valid_count,
        "ads_total_count": ads_total_count,
    }

    summary = {
        "strengths": site_synthesis.get("strengths", []),
        "weaknesses": site_synthesis.get("weaknesses", []),
        "top_opportunities": site_synthesis.get("top_opportunities", []),
    }

    return {
        "meta": meta,
        "summary": summary,
        "pages": pages,
        "tracking": tracking,
        "communication": {"pages": comunicacao["pages"], "site_synthesis": site_synthesis},
        "business_categories": site_synthesis["insights"],
        "keywords": termos,
        "opportunities": opportunities,
        "profile": perfil,
        "ads": ads,
        "llm_log": context.get("llm_log", []),
    }


def _attach_wait_reporter(llm_client: LLMClient, step: str, progress_queue: Optional["asyncio.Queue"]) -> None:
    """If this client can report rate-limit waits (OpenAICompatClient), wire it to surface
    "A aguardar limite gratuito da API (Xs)" as a progress event (secção D.13)."""
    if progress_queue is None or not hasattr(llm_client, "on_wait"):
        return

    def _on_wait(seconds: float, _step: str = step) -> None:
        try:
            progress_queue.put_nowait((_step, f"A aguardar limite gratuito da API ({int(seconds)}s)"))
        except Exception:  # noqa: BLE001 - a progress ping must never break the actual call
            pass

    llm_client.on_wait = _on_wait


async def _run_step(
    step: str,
    context: dict[str, Any],
    config: PipelineConfig,
    llm_factory: LLMFactory,
    browser: Browser,
    http_client: httpx.AsyncClient,
    duration_so_far: float,
    progress_queue: Optional["asyncio.Queue"],
) -> dict:
    if step == "crawl":
        result = await crawl_site(
            config.url,
            max_pages=config.max_pages,
            delay_seconds=config.delay_seconds,
            respect_robots=config.respect_robots,
            cache_dir=config.cache_dir,
            browser=browser,
            fast=config.fast,
        )
        return result.model_dump()

    pages = [PageData.model_validate(p) for p in context["crawl"]["pages"]]

    if step == "tracking":
        candidates = _pages_for_tracking(pages, config.tracking_pages)
        if not candidates:
            return empty_report()
        reports = []
        for candidate in candidates:
            tpage = await browser.new_page()
            try:
                reports.append(await detect_tracking(tpage, candidate.url))
            finally:
                await tpage.close()
        return merge_reports(reports)

    if step == "comunicacao":
        model = config.model_for_task.get("analise", "")
        llm = llm_factory("analise")
        _require_model(llm, "analise", model)
        _attach_wait_reporter(llm, step, progress_queue)
        # Páginas legais (privacidade, cookies, termos e condições) nunca têm nada a dizer
        # sobre problemas/desejos/CTA de um negócio - analisá-las só dilui a Comunicação com
        # ruído (secção 9). Se por algum motivo só existirem páginas legais, analisa-as de
        # qualquer forma em vez de falhar o passo inteiro sem nenhuma página.
        analysis_pages = [p for p in pages if p.type != "legal"] or pages
        total = len(analysis_pages)
        communications: list[PageCommunication] = []
        failed_pages: list[dict] = []
        for index, page in enumerate(analysis_pages, start=1):
            if progress_queue is not None:
                progress_queue.put_nowait((step, f"página {index} de {total}: {page.title or page.url}"))
            try:
                communications.append(await _analyze_page(llm, page, model=model, max_chars=config.max_page_chars))
            except Exception as exc:  # noqa: BLE001 - one broken page must never sink the rest
                print(f"[comunicacao] falhou a página '{page.id}' ({page.url}): {exc!r}")
                traceback.print_exc()
                failed_pages.append({"page_id": page.id, "url": page.url, "error": _format_error(exc)})

        if not communications:
            raise RuntimeError(
                f"Nenhuma das {total} páginas foi analisada com sucesso. Último erro: "
                f"{failed_pages[-1]['error'] if failed_pages else 'desconhecido'}."
            )

        return {
            "pages": [c.model_dump() for c in communications],
            "failed_pages": failed_pages,
            "pages_total": total,
            "pages_ok": len(communications),
        }

    if step == "sintese":
        comunicacao_result = context.get("comunicacao")
        if not comunicacao_result or not comunicacao_result.get("pages"):
            raise DependencyNotMetError("Depende da Comunicação, que não foi concluída.")
        model = config.model_for_task.get("analise", "")
        llm = llm_factory("analise")
        _require_model(llm, "analise", model)
        _attach_wait_reporter(llm, step, progress_queue)
        communications = [PageCommunication.model_validate(c) for c in comunicacao_result["pages"]]
        analyzed_ids = {c.page_id for c in communications}
        analyzed_pages = [p for p in pages if p.id in analyzed_ids] or pages
        synthesis = await synthesize_site(llm, analyzed_pages, communications, model=model)
        score = compute_communication_score(communications)
        return {"site_synthesis": synthesis.model_dump(), "communication_score": score}

    if step == "termos":
        model = config.model_for_task.get("keywords", "")
        llm = llm_factory("keywords")
        _require_model(llm, "keywords", model)
        _attach_wait_reporter(llm, step, progress_queue)
        # O Perfil (setor/produto ou serviço/geografia) já correu antes dos Termos (secção A):
        # é a fonte primária das sementes, para nunca derivarem de um resumo de página genérico
        # e acabarem a apontar para um setor errado.
        return await build_keywords(
            llm,
            http_client,
            pages,
            profile=context.get("perfil"),
            model=model,
            cache_dir=Path(config.cache_dir) / "keywords",
            use_alphabet=config.keyword_use_alphabet,
            delay_seconds=config.keyword_delay_seconds,
            overall_timeout=config.keyword_overall_timeout,
        )

    if step == "perfil":
        if not context.get("crawl", {}).get("pages"):
            raise DependencyNotMetError("Depende do rastreio do site, que não foi concluído.")
        model = config.model_for_task.get("perfil", "")
        llm = llm_factory("perfil")
        _require_model(llm, "perfil", model)
        _attach_wait_reporter(llm, step, progress_queue)
        profile = await extract_profile(llm, pages, model=model, max_chars=config.max_page_chars)
        return profile.model_dump()

    if step == "anuncios":
        if not context.get("perfil"):
            raise DependencyNotMetError("Depende do Perfil, que não foi concluído.")
        profile = context["perfil"]
        templates = select_templates_for_profile(profile["business_model"]["value"], profile.get("conteudo_forte", False))
        model = config.model_for_task.get("anuncios", "")
        llm = llm_factory("anuncios")
        _require_model(llm, "anuncios", model)
        _attach_wait_reporter(llm, step, progress_queue)
        prompts_output_dir = Path(config.output_dir) / context["domain"] / context["audit_id"] / "prompts_preenchidos"
        ads = await generate_all_ads(
            llm,
            templates,
            profile,
            pages,
            model=model,
            max_correction_rounds=config.max_correction_rounds,
            prompts_output_dir=prompts_output_dir,
        )
        return {"selected_templates": templates, "ads": ads}

    if step == "relatorio":
        for dep in ("tracking", "comunicacao", "sintese", "termos", "perfil"):
            if not context.get(dep):
                raise DependencyNotMetError(f"Depende de '{dep}', que não foi concluído.")
        return _assemble_report(context, config, duration_so_far)

    raise ValueError(f"passo de pipeline desconhecido: {step}")


async def run_pipeline(
    config: PipelineConfig,
    llm: Union[LLMClient, LLMFactory],
    *,
    browser: Optional[Browser] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> AsyncIterator[PipelineEvent]:
    """Run every pipeline step in order, yielding a PipelineEvent as each one starts,
    reports progress, and finishes. A step that raises is reported as an "error" event (with
    the full traceback printed server-side) and the pipeline continues with the next step,
    UNLESS that next step explicitly depends on the failed one (secção B.5), in which case it
    fails fast with a clear "Depende de X" message instead of an opaque KeyError.

    Pass an already-open `browser`/`http_client` to reuse them (tests inject a pinned browser
    and a mocked http_client so nothing here ever touches the real network); otherwise both
    are created and torn down internally. `llm` may be a single LLMClient (used for every
    task) or a `(task: str) -> LLMClient` factory, so different pipeline steps can use
    different backends/models (secção D.12).
    """
    llm_factory = _resolve_llm_factory(llm)
    domain = domain_of(config.url)
    audit_id = config.audit_id or new_audit_id()
    checkpoints = Checkpoints(config.output_dir, domain, audit_id)
    steps_to_run = config.only or STEPS

    context: dict[str, Any] = {"domain": domain, "audit_id": audit_id}
    pipeline_start = time.monotonic()

    async with AsyncExitStack() as stack:
        active_browser = browser
        if active_browser is None:
            pw = await stack.enter_async_context(async_playwright())
            active_browser = await launch_browser(pw, headless=True)
            stack.push_async_callback(active_browser.close)

        active_http_client = http_client
        if active_http_client is None:
            active_http_client = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=True, timeout=10.0))

        progress_queue: asyncio.Queue = asyncio.Queue()

        for step in STEPS:
            if step not in steps_to_run:
                continue

            cached = checkpoints.load(step) if config.resume else None
            if cached is not None:
                context[step] = cached
                yield PipelineEvent(step=step, status="done", data=cached, duration_seconds=0.0)
                continue

            yield PipelineEvent(step=step, status="running")
            start = time.monotonic()

            step_task = asyncio.ensure_future(
                _run_step(step, context, config, llm_factory, active_browser, active_http_client, time.monotonic() - pipeline_start, progress_queue)
            )
            get_task = asyncio.ensure_future(progress_queue.get())
            try:
                while True:
                    done, _pending = await asyncio.wait({step_task, get_task}, return_when=asyncio.FIRST_COMPLETED)
                    if get_task in done:
                        msg_step, message = get_task.result()
                        yield PipelineEvent(step=msg_step, status="progress", message=message, duration_seconds=time.monotonic() - start)
                        get_task = asyncio.ensure_future(progress_queue.get())
                    if step_task in done:
                        break
            finally:
                if not get_task.done():
                    get_task.cancel()
                    try:
                        await get_task
                    except (asyncio.CancelledError, Exception):
                        pass

            try:
                result = step_task.result()
            except Exception as exc:  # noqa: BLE001 - a broken step must never crash the whole audit
                print(f"[{step}] ERRO: {exc!r}")
                traceback.print_exc()
                yield PipelineEvent(step=step, status="error", error=_format_error(exc), duration_seconds=time.monotonic() - start)
                continue

            context[step] = result
            checkpoints.save(step, result)
            yield PipelineEvent(step=step, status="done", data=result, duration_seconds=time.monotonic() - start)

    if "relatorio" in context:
        (checkpoints.run_dir / "audit.json").write_text(
            json.dumps(context["relatorio"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _update_history_index(config.output_dir, domain, audit_id, context["relatorio"])


def _update_history_index(output_dir: Path | str, domain: str, audit_id: str, report: dict) -> None:
    index_path = Path(output_dir) / "index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    entries = [e for e in entries if not (e.get("domain") == domain and e.get("audit_id") == audit_id)]
    entries.insert(
        0,
        {
            "domain": domain,
            "audit_id": audit_id,
            "date": report["meta"]["audited_at"],
            "score": report["meta"]["communication_score"],
            "business_model": report["meta"]["business_model"],
        },
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def load_saved_audit(output_dir: Path | str, domain: str, audit_id: str) -> Optional[dict]:
    path = Path(output_dir) / domain / audit_id / "audit.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_history(output_dir: Path | str) -> list[dict]:
    index_path = Path(output_dir) / "index.json"
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text(encoding="utf-8"))


def delete_saved_audit(output_dir: Path | str, domain: str, audit_id: str) -> bool:
    import shutil

    run_dir = Path(output_dir) / domain / audit_id
    existed = run_dir.exists()
    if existed:
        shutil.rmtree(run_dir)

    index_path = Path(output_dir) / "index.json"
    if index_path.exists():
        entries = json.loads(index_path.read_text(encoding="utf-8"))
        entries = [e for e in entries if not (e.get("domain") == domain and e.get("audit_id") == audit_id)]
        index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    return existed


def find_resumable_audit_id(output_dir: Path | str, domain: str) -> Optional[str]:
    """The most recently touched run for this domain that has at least one checkpoint - what
    `--resume` without an explicit id picks up (secção 9: "retomar auditoria interrompida")."""
    domain_dir = Path(output_dir) / domain
    if not domain_dir.is_dir():
        return None
    candidates = [p for p in domain_dir.iterdir() if p.is_dir() and (p / "checkpoints").is_dir() and any((p / "checkpoints").iterdir())]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].name


def run_audit_sync(
    url: str,
    *,
    max_pages: int = 25,
    only: Optional[list[str]] = None,
    dry_run: bool = False,
    resume: bool = False,
    mock: bool = False,
    fast: bool = False,
) -> None:
    """Synchronous entry point for `python -m auditor audit <url>`. Prints one line per
    pipeline step transition as it happens."""
    import asyncio as _asyncio

    from auditor.appconfig import build_llm_client_for_task, load_config
    from auditor.appconfig import model_for_task as _model_for_task
    from auditor.llm.mock import MockLLMClient

    app_config = load_config()
    steps_to_run = only or None
    if fast:
        max_pages = min(max_pages, 5)
    if dry_run:
        print(f"[dry-run] passos que seriam executados: {', '.join(steps_to_run or STEPS)}")
        print(f"[dry-run] URL: {url} | max_pages: {max_pages} | resume: {resume} | mock: {mock} | fast: {fast}")
        return

    audit_id = None
    if resume:
        audit_id = find_resumable_audit_id(Path("output"), domain_of(url))
        if audit_id:
            print(f"A retomar a auditoria existente {domain_of(url)}/{audit_id}…")
        else:
            print("Nenhuma auditoria por concluir encontrada para este domínio; a começar uma nova.")

    if mock:
        mock_client = MockLLMClient()
        llm_factory: LLMFactory = lambda _task: mock_client  # noqa: E731
    else:
        llm_factory = lambda task: build_llm_client_for_task(app_config, task)  # noqa: E731

    config = PipelineConfig(
        url=url,
        max_pages=max_pages,
        audit_id=audit_id,
        delay_seconds=app_config["crawl"]["delay_seconds"],
        respect_robots=app_config["crawl"]["respect_robots"],
        tracking_pages=app_config["tracking"]["pages"],
        max_page_chars=app_config["llm"].get("max_page_chars", 4000),
        owner_services=list(app_config.get("owner_services", [])),
        model_for_task={} if mock else {task: _model_for_task(app_config, task) for task in ("analise", "keywords", "perfil", "anuncios")},
        fast=fast,
        only=steps_to_run,
        resume=resume,
    )

    async def _run() -> None:
        async for event in run_pipeline(config, llm_factory):
            if event.status == "running":
                print(f"[{event.step}] a processar…")
            elif event.status == "progress":
                print(f"[{event.step}] {event.message}")
            elif event.status == "done":
                print(f"[{event.step}] concluído em {event.duration_seconds:.1f}s")
            elif event.status == "error":
                print(f"[{event.step}] ERRO: {event.error}")

    _asyncio.run(_run())
