from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, Route

ABORT_PATTERNS = [
    re.compile(r"google-analytics\.com/g/collect"),
    re.compile(r"google-analytics\.com/j/collect"),
    re.compile(r"analytics\.google\.com/g/collect"),
    re.compile(r"googleadservices\.com/pagead/conversion"),
    re.compile(r"/pagead/1p-user-list"),
    re.compile(r"facebook\.com/tr"),
    re.compile(r"bat\.bing\.com/action"),
]

SCRIPT_PATTERNS = {
    "gtm": re.compile(r"googletagmanager\.com/gtm\.js\?id=([A-Za-z0-9-]+)"),
    "gtag": re.compile(r"googletagmanager\.com/gtag/js\?id=([A-Za-z0-9-]+)"),
    "ua_analytics_js": re.compile(r"google-analytics\.com/analytics\.js"),
    "fbevents": re.compile(r"connect\.facebook\.net/[^\"'?]*fbevents\.js"),
    "uet_bat": re.compile(r"bat\.bing\.com/bat\.js"),
}

CONSENT_ACCEPT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#cookiescript_accept",
    ".cky-btn-accept",
    "#cmplz-accept",
    "button:has-text('Aceitar todos')",
    "button:has-text('Aceitar Todos')",
    "button:has-text('Aceitar')",
    "button:has-text('Concordo')",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
]

CMP_SIGNATURES = [
    ("OneTrust", ["onetrust-banner-sdk", "onetrust-accept-btn-handler"]),
    ("Cookiebot", ["cybotcookiebotdialog", "cookiebot.com"]),
    ("CookieYes", ["cky-consent", "cookieyes"]),
    ("Complianz", ["cmplz-cookiebanner", "complianz"]),
    ("iubenda", ["iubenda-cs", "_iub.csConfiguration"]),
    ("Usercentrics", ["usercentrics-root"]),
]

GENERIC_BANNER_HINTS = ["aceitar todos", "aceitar cookies", "accept all", "aceitar", "concordo"]

EXTRA_SIGNATURES = [
    ("Microsoft Clarity", re.compile(r"clarity\.ms/tag")),
    ("Hotjar", re.compile(r"static\.hotjar\.com")),
    ("TikTok Pixel", re.compile(r"analytics\.tiktok\.com")),
    ("LinkedIn Insight", re.compile(r"snap\.licdn\.com")),
]

GTAG_CONFIG_RE = re.compile(r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]([A-Za-z0-9_-]+)['\"]")
FBQ_INIT_RE = re.compile(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d+)['\"]")
GA_CREATE_RE = re.compile(r"ga\(\s*['\"]create['\"]\s*,\s*['\"](UA-[0-9-]+)['\"]")
GTM_ID_RE = re.compile(r"googletagmanager\.com/gtm\.js\?id=(GTM-[A-Za-z0-9]+)")
ECOM_EVENT_RE = re.compile(r"['\"]event['\"]\s*:\s*['\"](view_item|add_to_cart|purchase|generate_lead)['\"]")

NOT_VISIBLE_NOTE = (
    "Esta ferramenta não vê tagging do lado do servidor (server-side tagging), a Conversions "
    "API do Meta, nem a configuração feita dentro das contas de Google Ads, Meta Ads ou "
    "Microsoft Advertising."
)

RUNTIME_PROBE_JS = """() => ({
  dataLayerLength: Array.isArray(window.dataLayer) ? window.dataLayer.length : null,
  gtmContainers: window.google_tag_manager ? Object.keys(window.google_tag_manager) : [],
  hasFbq: typeof window.fbq === 'function',
  hasUetq: typeof window.uetq !== 'undefined',
  hasGa: typeof window.ga === 'function',
})"""


class _RequestLog:
    def __init__(self) -> None:
        self.before: list[str] = []
        self.after: list[str] = []
        self.consent_given = False

    def record(self, url: str) -> None:
        (self.after if self.consent_given else self.before).append(url)


def _tid_prefix(url: str) -> Optional[str]:
    return (parse_qs(urlparse(url).query).get("tid") or [None])[0]


def _classify_collect(url: str) -> str:
    if "facebook.com/tr" in url:
        return "meta_pixel"
    if "bat.bing.com/action" in url:
        return "microsoft_uet"
    if "pagead/conversion" in url or "pagead/1p-user-list" in url:
        return "google_ads"
    if "google-analytics.com" in url or "analytics.google.com" in url:
        tid = _tid_prefix(url) or ""
        if tid.startswith("AW-"):
            return "google_ads"
        if tid.startswith("UA-"):
            return "ua"
        return "ga4"
    return "unknown"


def _consent_state(fired_before: bool, fired_after: bool, loaded_in_code: bool) -> str:
    if fired_before:
        return "A disparar sem consentimento"
    if fired_after:
        return "A disparar só após consentimento"
    if loaded_in_code:
        return "No código, sem disparo observado"
    return "Não detetado"


def _evidence_lines(urls: list[str], extra: Optional[list[str]] = None) -> list[str]:
    lines = [f"Pedido de rede para {u}" for u in dict.fromkeys(urls)]
    if extra:
        lines.extend(extra)
    return lines[:6]


def _detect_cmp(html: str) -> dict:
    lower = html.lower()
    for name, markers in CMP_SIGNATURES:
        if any(marker.lower() in lower for marker in markers):
            return {"name": name, "cookie_banner_detected": True}
    generic = any(hint in lower for hint in GENERIC_BANNER_HINTS)
    return {
        "name": "Não identificada (banner próprio, sem CMP reconhecida)" if generic else "Não detetado",
        "cookie_banner_detected": generic,
    }


def _consent_mode_detected(urls: list[str]) -> bool:
    return any("gcd=" in u or "gcs=" in u for u in urls)


async def detect_tracking(page: Page, url: str, *, consent_selectors: Optional[list[str]] = None) -> dict:
    """Two-pass tracking detection for one page: navigate, capture requests, try to accept
    cookies, then capture requests again. Every request matched by ABORT_PATTERNS is recorded
    as evidence and then aborted, so nothing is ever actually sent to the site's own Analytics,
    Ads or Pixel. Everything else (tracker scripts themselves) is left to load normally.
    """
    consent_selectors = consent_selectors or CONSENT_ACCEPT_SELECTORS
    log = _RequestLog()
    loaded: dict[str, list[str]] = defaultdict(list)

    async def handle_route(route: Route) -> None:
        req_url = route.request.url
        if any(p.search(req_url) for p in ABORT_PATTERNS):
            log.record(req_url)
            await route.abort()
            return
        for key, pattern in SCRIPT_PATTERNS.items():
            if pattern.search(req_url):
                loaded[key].append(req_url)
        # fallback() (not continue_()) so a handler registered earlier - e.g. a test's
        # stub for googletagmanager.com - gets a chance to resolve the request itself;
        # with no such handler this still ends in a normal network request, same as continue_().
        await route.fallback()

    await page.route("**/*", handle_route)
    try:
        await page.goto(url, wait_until="load", timeout=20000)
        await page.wait_for_timeout(400)

        html = await page.content()
        runtime = await page.evaluate(RUNTIME_PROBE_JS)

        for selector in consent_selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0 or not await locator.first.is_visible():
                    continue
                log.consent_given = True
                await locator.first.click(timeout=3000)
                await page.wait_for_timeout(600)
                break
            except Exception:  # noqa: BLE001 - a selector that doesn't apply on this page/CMP is not an error
                continue
    finally:
        await page.unroute("**/*", handle_route)

    return _build_report(html, runtime, log, loaded)


def _build_report(html: str, runtime: dict, log: _RequestLog, loaded: dict[str, list[str]]) -> dict:
    before_by_platform: dict[str, list[str]] = defaultdict(list)
    after_by_platform: dict[str, list[str]] = defaultdict(list)
    for u in log.before:
        before_by_platform[_classify_collect(u)].append(u)
    for u in log.after:
        after_by_platform[_classify_collect(u)].append(u)

    gtag_ids = GTAG_CONFIG_RE.findall(html)
    ga4_ids = [i for i in gtag_ids if i.startswith("G-")]
    aw_ids = [i for i in gtag_ids if i.startswith("AW-")]
    ua_ids = [i for i in gtag_ids if i.startswith("UA-")] + GA_CREATE_RE.findall(html)
    fbq_ids = FBQ_INIT_RE.findall(html)
    gtm_ids = GTM_ID_RE.findall(html) or [u.split("id=")[-1].split("&")[0] for u in loaded.get("gtm", [])]

    def state_for(platform: str, loaded_flag: bool) -> str:
        return _consent_state(bool(before_by_platform.get(platform)), bool(after_by_platform.get(platform)), loaded_flag)

    def evidence_for(platform: str, extra: Optional[list[str]] = None) -> list[str]:
        return _evidence_lines(before_by_platform.get(platform, []) + after_by_platform.get(platform, []), extra)

    ga4_loaded_in_code = bool(ga4_ids) or bool(loaded.get("gtag")) or runtime.get("dataLayerLength") is not None
    ua_loaded_in_code = bool(ua_ids) or bool(loaded.get("ua_analytics_js")) or runtime.get("hasGa")
    aw_loaded_in_code = bool(aw_ids)
    fbq_loaded_in_code = bool(fbq_ids) or bool(loaded.get("fbevents")) or runtime.get("hasFbq")
    uet_loaded_in_code = bool(loaded.get("uet_bat")) or runtime.get("hasUetq")

    report: dict = {
        "ga4": {
            "platform": "Google Analytics 4",
            "detected": bool(ga4_ids) or bool(before_by_platform.get("ga4")) or bool(after_by_platform.get("ga4")),
            "id": ga4_ids[0] if ga4_ids else None,
            "state": state_for("ga4", ga4_loaded_in_code),
            "evidence": evidence_for("ga4"),
        },
        "ua": {
            "platform": "Universal Analytics",
            "detected": bool(ua_ids) or bool(before_by_platform.get("ua")) or bool(after_by_platform.get("ua")),
            "id": ua_ids[0] if ua_ids else None,
            "state": state_for("ua", ua_loaded_in_code),
            "evidence": evidence_for("ua"),
        },
        "gtm": {
            "platform": "Google Tag Manager",
            "detected": bool(gtm_ids),
            "id": gtm_ids[0] if gtm_ids else None,
            "state": "Detetado" if gtm_ids else "Não detetado",
            "evidence": [f"Script googletagmanager.com/gtm.js?id={gtm_ids[0]}"] if gtm_ids else [],
        },
        "google_ads": {
            "platform": "Google Ads",
            "detected": bool(aw_ids) or bool(before_by_platform.get("google_ads")) or bool(after_by_platform.get("google_ads")),
            "id": aw_ids[0] if aw_ids else None,
            "state": state_for("google_ads", aw_loaded_in_code),
            "evidence": evidence_for("google_ads"),
        },
        "meta_pixel": {
            "platform": "Meta Pixel",
            "detected": bool(fbq_ids) or bool(before_by_platform.get("meta_pixel")) or bool(after_by_platform.get("meta_pixel")),
            "id": fbq_ids[0] if fbq_ids else None,
            "state": state_for("meta_pixel", fbq_loaded_in_code),
            "evidence": evidence_for("meta_pixel"),
        },
        "microsoft_uet": {
            "platform": "Microsoft Advertising (UET)",
            "detected": bool(before_by_platform.get("microsoft_uet")) or bool(after_by_platform.get("microsoft_uet")) or uet_loaded_in_code,
            "id": None,
            "state": state_for("microsoft_uet", uet_loaded_in_code),
            "evidence": evidence_for("microsoft_uet"),
        },
        "consent_mode": {
            "detected": _consent_mode_detected(log.before + log.after),
            "state": "Detetado" if _consent_mode_detected(log.before + log.after) else "Não detetado",
        },
        "cmp": _detect_cmp(html),
        "extras": [
            {"platform": name, "detected": bool(pattern.search(html)), "state": "Detetado" if pattern.search(html) else "Não detetado"}
            for name, pattern in EXTRA_SIGNATURES
        ],
        "ecommerce_events_in_datalayer": sorted(set(ECOM_EVENT_RE.findall(html))),
        "not_visible_note": NOT_VISIBLE_NOTE,
    }
    return report


def merge_reports(reports: list[dict]) -> dict:
    """Merge per-page tracking reports into one site-level report.

    For state fields, the most revealing state wins (firing without consent > firing only
    after consent > present but silent > not detected); evidence and extras are combined.
    """
    if not reports:
        raise ValueError("merge_reports needs at least one per-page report")
    if len(reports) == 1:
        return reports[0]

    state_rank = {
        "A disparar sem consentimento": 3,
        "A disparar só após consentimento": 2,
        "No código, sem disparo observado": 1,
        "Detetado": 1,
        "Não detetado": 0,
    }

    merged: dict = {}
    for key in ("ga4", "ua", "gtm", "google_ads", "meta_pixel", "microsoft_uet"):
        entries = [r[key] for r in reports if key in r]
        best = max(entries, key=lambda e: state_rank.get(e["state"], 0))
        merged[key] = {
            **best,
            "id": next((e["id"] for e in entries if e.get("id")), None),
            "detected": any(e["detected"] for e in entries),
            "evidence": list(dict.fromkeys(line for e in entries for line in e["evidence"])) [:6],
        }

    merged["consent_mode"] = {
        "detected": any(r["consent_mode"]["detected"] for r in reports),
        "state": "Detetado" if any(r["consent_mode"]["detected"] for r in reports) else "Não detetado",
    }
    merged["cmp"] = next((r["cmp"] for r in reports if r["cmp"]["cookie_banner_detected"]), reports[0]["cmp"])

    extras_by_platform: dict[str, bool] = defaultdict(bool)
    for r in reports:
        for extra in r["extras"]:
            extras_by_platform[extra["platform"]] |= extra["detected"]
    merged["extras"] = [{"platform": p, "detected": d, "state": "Detetado" if d else "Não detetado"} for p, d in extras_by_platform.items()]

    merged["ecommerce_events_in_datalayer"] = sorted(set().union(*(set(r["ecommerce_events_in_datalayer"]) for r in reports)))
    merged["not_visible_note"] = NOT_VISIBLE_NOTE
    return merged
