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
    # verificado antes de tudo o resto: uma página legal nunca deve ser classificada como
    # "servico"/"produto" só porque o caminho contém coincidentemente uma dessas palavras.
    "legal": [
        "privacy-policy", "privacidade", "politica-de-privacidade", "cookies", "cookie-policy",
        "termos-e-condicoes", "termos-condicoes", "terms-and-conditions", "terms-of-service",
        "aviso-legal", "condicoes-gerais", "rgpd", "gdpr",
    ],
    "precos": ["preco", "precos", "tarifario", "pricing", "plano", "planos"],
    "servico": ["servico", "servicos", "service", "services"],
    "produto": ["produto", "produtos", "product", "products"],
    "categoria": ["categoria", "categorias", "category", "colecao", "collection"],
    "sobre": ["sobre", "about", "quem-somos"],
    "contacto": ["contacto", "contactos", "contact"],
    "faq": ["faq", "perguntas-frequentes", "duvidas"],
    "landing": ["landing", "lp-", "promo"],
    # Arquivos de blog (tags/categorias) e newsletter nunca têm nada a dizer sobre o negócio em
    # si - são ruído puro para a análise de Comunicação (secção D.2), por isso são detetados
    # antes do tipo genérico "blog"/"categoria".
    "blog_arquivo": ["tag/", "tags/", "etiqueta/", "blog/category", "blog/categoria", "categorias-blog/", "newsletter"],
    "blog": ["blog", "noticias", "artigo", "news", "article"],
}
PRIORITY_ORDER = ["home", "servico", "produto", "categoria", "precos", "sobre", "contacto", "faq", "landing", "blog", "legal", "blog_arquivo", "outro"]
# Prioridade usada em auditorias --fast (secção 9): páginas institucionais primeiro, o resto
# (incluindo blog/legal) só depois de esgotar essas - nunca vale a pena gastar as poucas
# páginas do modo rápido em conteúdo secundário.
FAST_PRIORITY_ORDER = ["home", "servico", "sobre", "contacto", "produto", "categoria", "precos", "faq", "landing", "blog", "legal", "blog_arquivo", "outro"]
# Tipos de página que nunca acrescentam nada à análise de Comunicação/pontuação (secção D.2):
# páginas legais, notícias/artigos datados e arquivos de blog (tags/categorias) ou newsletter.
NON_ANALYSIS_PAGE_TYPES = {"legal", "blog", "blog_arquivo"}
# Páginas centrais ao funil de conversão (secção D.3): a pontuação de comunicação é a média
# só destas, nunca de páginas institucionais/secundárias como FAQ.
CONVERSION_PAGE_TYPES = {"home", "servico", "produto", "landing", "precos", "contacto"}
# Prioridade para escolher, de entre as páginas rastreadas, quais analisar quando há mais do
# que crawl.max_analyzed_pages (secção D.3).
ANALYSIS_PRIORITY_ORDER = ["home", "servico", "produto", "sobre", "contacto", "precos", "categoria", "faq", "landing", "outro"]

# Códigos de língua ISO 639-1 mais comuns em prefixos de URL (ex.: /en/, /fr/) - usados para
# detetar versões noutra língua do mesmo conteúdo (secção D.1). Não é uma lista exaustiva de
# todas as línguas do mundo, só das que costumam aparecer como prefixo de path em sites PT.
COMMON_LANGUAGE_CODES = {
    "en", "es", "fr", "de", "it", "nl", "pl", "ru", "zh", "ja", "ko", "ar",
    "sv", "da", "no", "fi", "tr", "el", "cs", "ro", "hu", "uk",
}
LANGUAGE_PATH_PREFIX_RE = re.compile(r"^/([a-z]{2})(?:-[a-z]{2})?(/.*)?$", re.IGNORECASE)

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


def page_priority(page_type: str, *, fast: bool = False) -> int:
    order = FAST_PRIORITY_ORDER if fast else PRIORITY_ORDER
    try:
        return order.index(page_type)
    except ValueError:
        return len(order)


def _drop_foreign_language_duplicates(pages: list[PageData], *, languages: Optional[list[str]] = None) -> list[PageData]:
    """Ignora /en/, /fr/, /de/, /es/... (secção D.1) quando a versão na língua principal do
    site (config crawl.languages, por defeito ["pt"]) também foi rastreada: o mesmo conteúdo
    noutra língua só duplicaria a análise de comunicação/perfil, sem acrescentar nada. Um
    prefixo de path só conta como "outra língua" se for um código ISO 639-1 conhecido
    (COMMON_LANGUAGE_CODES) - nunca um segmento de path qualquer de duas letras."""
    primary_languages = {lang.lower() for lang in (languages or ["pt"])}
    known_paths = {urlparse(p.url).path or "/" for p in pages}

    def _has_primary_language_equivalent(path: str) -> bool:
        match = LANGUAGE_PATH_PREFIX_RE.match(path)
        if not match:
            return False
        lang = match.group(1).lower()
        if lang in primary_languages or lang not in COMMON_LANGUAGE_CODES:
            return False
        primary_path = match.group(2) or "/"
        return primary_path in known_paths

    return [p for p in pages if not _has_primary_language_equivalent(urlparse(p.url).path or "/")]


def _dedupe_pages_by_title_and_content(pages: list[PageData]) -> list[PageData]:
    """Deduplica por título e conteúdo (secção D.2): duas páginas com o mesmo título e o mesmo
    início de texto principal são a mesma página em duplicado (paginação, parâmetros, etc.)."""
    seen: set[tuple[str, str]] = set()
    result: list[PageData] = []
    for page in pages:
        title = (page.title or "").strip().lower()
        key = (title, (page.main_text or "")[:200].strip().lower())
        # Sem título nenhum não há sinal suficiente para decidir que é duplicada - mantém-la
        # sempre em vez de arriscar colapsar páginas distintas só porque nenhuma tem título.
        if title and key in seen:
            continue
        if title:
            seen.add(key)
        result.append(page)
    return result


def _analysis_priority(page_type: str) -> int:
    try:
        return ANALYSIS_PRIORITY_ORDER.index(page_type)
    except ValueError:
        return len(ANALYSIS_PRIORITY_ORDER)


def select_pages_for_analysis(pages: list[PageData], *, max_analyzed_pages: int = 12) -> list[PageData]:
    """Páginas enviadas à Comunicação/Perfil/Anúncios (secção D.2/D.3): exclui páginas legais,
    notícias/artigos datados e arquivos de blog/newsletter, deduplica por título+conteúdo, e
    cobra no máximo `max_analyzed_pages`, priorizando home/serviços/produtos/sobre/contacto.
    Nunca devolve uma lista vazia enquanto existir pelo menos uma página rastreada."""
    candidates = _dedupe_pages_by_title_and_content([p for p in pages if p.type not in NON_ANALYSIS_PAGE_TYPES])
    if not candidates:
        candidates = _dedupe_pages_by_title_and_content(pages)
    ordered = sorted(candidates, key=lambda p: (_analysis_priority(p.type), p.url))
    return ordered[:max_analyzed_pages]


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
    fast: bool = False,
    languages: Optional[list[str]] = None,
) -> CrawlResult:
    """Discover and extract up to `max_pages` same-domain pages, starting from `start_url`.

    Network errors on individual pages are recorded in `CrawlResult.errors` and never abort
    the rest of the crawl. Results are cached per URL under `cache_dir`; pass `refresh=True`
    to bypass the cache. Pass an already-launched `browser` to reuse it (e.g. across pipeline
    steps, or a pinned browser in tests) instead of launching a new one for this call. With
    `fast=True`, páginas institucionais (home/serviços/sobre/contacto) têm prioridade sobre
    blog/legal/outras, para as poucas páginas do modo rápido serem sempre as mais relevantes
    para uma auditoria de comunicação/anúncios (secção 9).
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
                queue.sort(key=lambda u: (page_priority(classify_page_type(u), fast=fast), u))
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

    return CrawlResult(domain=domain, pages=_drop_foreign_language_duplicates(pages, languages=languages), errors=errors)
