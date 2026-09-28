from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse
import urllib.robotparser as robotparser

import httpx
from playwright.async_api import Browser
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from auditor.browser import launch_browser

SKIP_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".mp3",
    ".css", ".js", ".xml", ".json",
}
SKIP_PATH_KEYWORDS = [
    "carrinho", "cart", "checkout", "login", "signin", "sign-in",
    "minha-conta", "my-account", "wp-admin", "wp-login", "logout",
]

PAGE_TYPE_KEYWORDS = {
    "precos": ["preco", "precos", "tarifario", "pricing", "plano", "planos"],
    "servico": ["servico", "servicos", "service", "services"],
    "produto": ["produto", "produtos", "product", "products"],
    "categoria": ["categoria", "categorias", "category", "colecao", "collection"],
    "sobre": ["sobre", "about", "quem-somos"],
    "contacto": ["contacto", "contactos", "contact"],
    "faq": ["faq", "perguntas-frequentes", "duvidas"],
    "landing": ["landing", "lp-", "promo"],
    "blog": ["blog", "noticias", "artigo", "news", "article"],
}
PRIORITY_ORDER = ["home", "servico", "produto", "categoria", "precos", "sobre", "contacto", "faq", "landing", "blog", "outro"]

TESTIMONIAL_KEYWORDS = ["testimonial", "depoimento", "testemunho", "review"]
BADGE_KEYWORDS = ["garantia", "satisfação garantida", "certificado", "selo", "iso 9001", "devolução gratuita"]
PRICE_RE = re.compile(r"(?:€\s?\d[\d.,]*|\d[\d.,]*\s?€)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+351\s?)?(?:2\d{8}|9\d{2}(?:[\s.-]?\d{3}){2})")
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)

PLATFORM_SIGNATURES = [
    ("WooCommerce", ["woocommerce"]),
    ("WordPress", ["wp-content", "wp-json", "wp-includes"]),
    ("Shopify", ["cdn.shopify.com", "shopify.theme"]),
    ("PrestaShop", ["prestashop"]),
    ("Magento", ["/skin/frontend/", "mage.cookies"]),
    ("Wix", ["static.wixstatic.com"]),
]


class PageData(BaseModel):
    id: str
    url: str
    type: str
    title: str = ""
    meta_description: str = ""
    h1: list[str] = Field(default_factory=list)
    h2: list[str] = Field(default_factory=list)
    h3: list[str] = Field(default_factory=list)
    main_text: str = ""
    ctas: list[str] = Field(default_factory=list)
    forms: list[dict] = Field(default_factory=list)
    testimonials: list[str] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)
    prices: list[str] = Field(default_factory=list)
    contacts: dict = Field(default_factory=dict)
    schema_types: list[str] = Field(default_factory=list)
    platform: Optional[str] = None


class CrawlResult(BaseModel):
    domain: str
    pages: list[PageData] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def is_same_domain(url: str, domain: str) -> bool:
    try:
        return urlparse(url).netloc.lower() == domain.lower()
    except ValueError:
        return False


def should_skip_url(url: str) -> bool:
    parsed = urlparse(url)
    if Path(parsed.path).suffix.lower() in SKIP_EXTENSIONS:
        return True
    path_lower = parsed.path.lower()
    return any(kw in path_lower for kw in SKIP_PATH_KEYWORDS)


def classify_page_type(url: str) -> str:
    path = urlparse(url).path.strip("/").lower()
    if path == "":
        return "home"
    for page_type, keywords in PAGE_TYPE_KEYWORDS.items():
        if any(kw in path for kw in keywords):
            return page_type
    return "outro"


def page_priority(page_type: str) -> int:
    try:
        return PRIORITY_ORDER.index(page_type)
    except ValueError:
        return len(PRIORITY_ORDER)


def _slugify(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return "home"
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return slug or "home"


async def _fetch_robots(client: httpx.AsyncClient, root: str) -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    try:
        resp = await client.get(urljoin(root, "/robots.txt"))
        rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except httpx.HTTPError:
        rp.parse([])
    return rp


async def _fetch_sitemap_urls(client: httpx.AsyncClient, root: str) -> list[str]:
    try:
        resp = await client.get(urljoin(root, "/sitemap.xml"))
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    return LOC_RE.findall(resp.text)


def _extract_main_text(tree: HTMLParser) -> str:
    body = tree.css_first("body")
    if body is None:
        return ""
    for selector in ("nav", "footer", "[class*=cookie]", "[id*=cookie]", "[class*=consent]", "[id*=consent]", "script", "style", "noscript"):
        for node in body.css(selector):
            node.decompose()
    text = re.sub(r"\s+", " ", body.text(separator=" ", strip=True)).strip()
    return text[:6000]


def _extract_ctas(tree: HTMLParser) -> list[str]:
    seen: list[str] = []
    for node in tree.css("button, a.btn, a.button, a[class*=cta], a[role=button], button[class*=cta]"):
        text = node.text(strip=True)
        if text and text not in seen:
            seen.append(text)
    return seen[:15]


def _extract_forms(tree: HTMLParser) -> list[dict]:
    forms = []
    for form in tree.css("form"):
        fields = []
        for field in form.css("input, select, textarea"):
            ftype = field.attributes.get("type", "text")
            if ftype in ("hidden", "submit", "button"):
                continue
            label = field.attributes.get("placeholder") or field.attributes.get("name") or ftype
            fields.append(label)
        forms.append({"field_count": len(fields), "fields": fields})
    return forms


def _extract_testimonials(tree: HTMLParser) -> list[str]:
    results: list[str] = []
    for keyword in TESTIMONIAL_KEYWORDS:
        for node in tree.css(f"[class*={keyword}], [id*={keyword}]"):
            text = node.text(strip=True)
            if text and text not in results:
                results.append(text[:200])
    return results[:5]


def _extract_badges(html: str) -> list[str]:
    lower = html.lower()
    return [kw for kw in BADGE_KEYWORDS if kw in lower]


def _extract_prices(text: str) -> list[str]:
    unique: list[str] = []
    for found in PRICE_RE.findall(text):
        found = found.strip()
        if found not in unique:
            unique.append(found)
    return unique[:10]


def _extract_contacts(html: str) -> dict:
    contacts: dict = {}
    email_match = EMAIL_RE.search(html)
    if email_match:
        contacts["email"] = email_match.group(0)
    phone_match = PHONE_RE.search(html)
    if phone_match:
        contacts["phone"] = phone_match.group(0)
    return contacts


def _extract_schema_types(tree: HTMLParser) -> list[str]:
    types: list[str] = []
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except (json.JSONDecodeError, TypeError):
            continue
        for entry in data if isinstance(data, list) else [data]:
            if isinstance(entry, dict) and "@type" in entry:
                value = entry["@type"]
                types.extend(value if isinstance(value, list) else [value])
    return types


def _detect_platform(html: str) -> Optional[str]:
    lower = html.lower()
    for name, markers in PLATFORM_SIGNATURES:
        if any(marker.lower() in lower for marker in markers):
            return name
    return None


def _extract_page_data(html: str, url: str, page_id: str) -> tuple[PageData, list[str]]:
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else ""
    meta_node = tree.css_first("meta[name=description]")
    meta_description = (meta_node.attributes.get("content") or "").strip() if meta_node else ""

    links = [urljoin(url, a.attributes["href"]) for a in tree.css("a[href]") if a.attributes.get("href")]
    main_text = _extract_main_text(tree)

    page = PageData(
        id=page_id,
        url=url,
        type=classify_page_type(url),
        title=title,
        meta_description=meta_description,
        h1=[n.text(strip=True) for n in tree.css("h1")],
        h2=[n.text(strip=True) for n in tree.css("h2")],
        h3=[n.text(strip=True) for n in tree.css("h3")],
        main_text=main_text,
        ctas=_extract_ctas(tree),
        forms=_extract_forms(tree),
        testimonials=_extract_testimonials(tree),
        badges=_extract_badges(html),
        prices=_extract_prices(main_text),
        contacts=_extract_contacts(html),
        schema_types=_extract_schema_types(tree),
        platform=_detect_platform(html),
    )
    return page, links


async def crawl_site(
    start_url: str,
    *,
    max_pages: int = 25,
    delay_seconds: float = 1.5,
    respect_robots: bool = True,
    cache_dir: str | Path = ".cache",
    refresh: bool = False,
    browser: Optional[Browser] = None,
) -> CrawlResult:
    """Discover and extract up to `max_pages` same-domain pages, starting from `start_url`.

    Network errors on individual pages are recorded in `CrawlResult.errors` and never abort
    the rest of the crawl. Results are cached per URL under `cache_dir`; pass `refresh=True`
    to bypass the cache. Pass an already-launched `browser` to reuse it (e.g. across pipeline
    steps, or a pinned browser in tests) instead of launching a new one for this call.
    """
    if "://" not in start_url:
        start_url = f"https://{start_url}"
    parsed_start = urlparse(start_url)
    root = f"{parsed_start.scheme}://{parsed_start.netloc}"
    domain = parsed_start.netloc

    cache_path = Path(cache_dir) / domain
    cache_path.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        robots = await _fetch_robots(client, root) if respect_robots else None
        sitemap_urls = await _fetch_sitemap_urls(client, root)

    discovered: "OrderedDict[str, None]" = OrderedDict()
    discovered[normalize_url(root + "/")] = None
    for u in sitemap_urls:
        if is_same_domain(u, domain) and not should_skip_url(u):
            discovered.setdefault(normalize_url(u), None)

    pages: list[PageData] = []
    visited: set[str] = set()

    async def _run(active_browser: Browser) -> None:
        bpage = await active_browser.new_page()
        try:
            while len(pages) < max_pages:
                queue = [u for u in discovered if u not in visited]
                if not queue:
                    break
                queue.sort(key=lambda u: (page_priority(classify_page_type(u)), u))
                url = queue[0]
                visited.add(url)

                if robots is not None and not robots.can_fetch("*", url):
                    continue

                cache_file = cache_path / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
                if cache_file.exists() and not refresh:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                    page_data = PageData.model_validate(cached["page"])
                    links = cached["links"]
                else:
                    try:
                        response = await bpage.goto(url, wait_until="load", timeout=20000)
                    except PlaywrightTimeoutError:
                        errors.append(f"Tempo esgotado ao carregar {url}")
                        continue
                    except Exception as exc:  # noqa: BLE001 - a target site's own failures must never crash the crawl
                        errors.append(f"Erro ao carregar {url}: {exc}")
                        continue
                    if response is not None and response.status >= 400:
                        errors.append(f"{url} devolveu o estado {response.status}")
                        continue

                    final_url = normalize_url(bpage.url)
                    html = await bpage.content()
                    page_data, links = _extract_page_data(html, final_url, _slugify(final_url))
                    cache_file.write_text(
                        json.dumps({"page": page_data.model_dump(), "links": links}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    if delay_seconds:
                        await asyncio.sleep(delay_seconds)

                pages.append(page_data)
                for link in links:
                    if is_same_domain(link, domain) and not should_skip_url(link):
                        discovered.setdefault(normalize_url(link), None)
        finally:
            await bpage.close()

    if browser is not None:
        await _run(browser)
    else:
        async with async_playwright() as pw:
            owned_browser = await launch_browser(pw, headless=True)
            try:
                await _run(owned_browser)
            finally:
                await owned_browser.close()

    return CrawlResult(domain=domain, pages=pages, errors=errors)
