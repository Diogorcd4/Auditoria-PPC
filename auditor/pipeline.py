from __future__ import annotations

import json
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import httpx
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel

from auditor.analysis import PageCommunication, compute_communication_score, synthesize_site
from auditor.analysis import analyze_page as _analyze_page
from auditor.browser import launch_browser
from auditor.crawler import PageData, crawl_site
from auditor.keywords import build_keywords
from auditor.llm.base import LLMClient
from auditor.opportunities import derive_opportunities
from auditor.profile import extract_profile
from auditor.prompts import select_templates_for_profile
from auditor.tracking import detect_tracking, empty_report, merge_reports

STEPS = ["crawl", "tracking", "comunicacao", "sintese", "termos", "perfil", "anuncios", "relatorio"]

DEFAULT_OWNER_SERVICES = ["Google Ads", "Meta Ads", "Microsoft Advertising", "Tracking e Analytics", "Copy e conversão"]

TRACKING_PAGE_TYPE_PRIORITY = ["landing", "servico", "produto", "contacto", "precos"]


class PipelineEvent(BaseModel):
    step: str
    status: str  # running | done | error
    data: Optional[dict] = None
    error: Optional[str] = None
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
    owner_services: list[str] = field(default_factory=lambda: list(DEFAULT_OWNER_SERVICES))
    model_for_task: dict[str, str] = field(default_factory=dict)
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


async def _run_step(
    step: str,
    context: dict[str, Any],
    config: PipelineConfig,
    llm: LLMClient,
    browser: Browser,
    http_client: httpx.AsyncClient,
    duration_so_far: float,
) -> dict:
    if step == "crawl":
        result = await crawl_site(
            config.url,
            max_pages=config.max_pages,
            delay_seconds=config.delay_seconds,
            respect_robots=config.respect_robots,
            cache_dir=config.cache_dir,
            browser=browser,
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
        communications = [await _analyze_page(llm, p, model=model) for p in pages]
        return {"pages": [c.model_dump() for c in communications]}

    if step == "sintese":
        model = config.model_for_task.get("analise", "")
        communications = [PageCommunication.model_validate(c) for c in context["comunicacao"]["pages"]]
        synthesis = await synthesize_site(llm, pages, communications, model=model)
        score = compute_communication_score(communications)
        return {"site_synthesis": synthesis.model_dump(), "communication_score": score}

    if step == "termos":
        model = config.model_for_task.get("keywords", "")
        return await build_keywords(
            llm,
            http_client,
            pages,
            model=model,
            cache_dir=Path(config.cache_dir) / "keywords",
            use_alphabet=config.keyword_use_alphabet,
            delay_seconds=config.keyword_delay_seconds,
        )

    if step == "perfil":
        model = config.model_for_task.get("perfil", "")
        profile = await extract_profile(llm, pages, model=model)
        return profile.model_dump()

    if step == "anuncios":
        # A geração real dos anúncios é implementada na Fase 6; por agora só regista quais
        # templates seriam usados, para que o perfil já apareça correto na interface.
        profile = context["perfil"]
        templates = select_templates_for_profile(profile["business_model"]["value"], profile.get("conteudo_forte", False))
        return {"selected_templates": templates, "ads": {}}

    if step == "relatorio":
        return _assemble_report(context, config, duration_so_far)

    raise ValueError(f"passo de pipeline desconhecido: {step}")


async def run_pipeline(
    config: PipelineConfig,
    llm: LLMClient,
    *,
    browser: Optional[Browser] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> AsyncIterator[PipelineEvent]:
    """Run every pipeline step in order, yielding a PipelineEvent as each one starts and
    finishes. A step that raises is reported as an "error" event and the pipeline continues
    with the next step (secção 8: um erro numa etapa nunca aborta as restantes).

    Pass an already-open `browser`/`http_client` to reuse them (tests inject a pinned browser
    and a mocked http_client so nothing here ever touches the real network); otherwise both
    are created and torn down internally.
    """
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
            try:
                result = await _run_step(step, context, config, llm, active_browser, active_http_client, time.monotonic() - pipeline_start)
            except Exception as exc:  # noqa: BLE001 - a broken step must never crash the whole audit
                yield PipelineEvent(step=step, status="error", error=str(exc), duration_seconds=time.monotonic() - start)
                continue

            context[step] = result
            checkpoints.save(step, result)
            yield PipelineEvent(step=step, status="done", data=result, duration_seconds=time.monotonic() - start)

    if "relatorio" in context:
        (checkpoints.run_dir / "audit.json").write_text(
            json.dumps(context["relatorio"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _update_history_index(config.output_dir, context["relatorio"])


def _update_history_index(output_dir: Path | str, report: dict) -> None:
    index_path = Path(output_dir) / "index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    entries.insert(
        0,
        {
            "id": f"{report['meta']['domain']}/{report['meta']['audited_at']}",
            "domain": report["meta"]["domain"],
            "date": report["meta"]["audited_at"],
            "score": report["meta"]["communication_score"],
        },
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
