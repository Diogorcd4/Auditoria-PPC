from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from auditor.crawler import PageData
from auditor.llm.base import LLMClient
from auditor.llm_json import PT_PT_INSTRUCTION, generate_json, wrap_site_content

PROFILE_FIELDS = [
    "sector",
    "empresa",
    "publico",
    "descricao_publico",
    "acao",
    "geografia",
    "landing_page_url",
    "pagina_destino",
    "produto_ou_servico",
    "preco",
    "idade",
    "perfil",
    "nivel_poder_compra",
    "motivacao",
    "objecao",
    "comportamento_online",
    "objetivo_conversao",
    "fonte_dados_clientes",
    "fonte_dados_subscritores",
    "conteudo_a_promover",
    "objectivo_pos_trafego",
    "motivacao_clique",
    "canais",
]

DEFAULT_GEOGRAFIA = "Portugal"
PRICE_NOT_ON_SITE = "não indicado no site"

LANDING_TYPE_PRIORITY = ["landing", "servico", "produto", "home"]


class ProfileField(BaseModel):
    value: str
    origin: Literal["site", "inferido", "default", "manual"]
    confidence: float = Field(ge=0.0, le=1.0)


class SiteProfile(BaseModel):
    sector: ProfileField
    empresa: ProfileField
    publico: ProfileField
    descricao_publico: ProfileField
    acao: ProfileField
    geografia: ProfileField
    landing_page_url: ProfileField
    pagina_destino: ProfileField
    produto_ou_servico: ProfileField
    preco: ProfileField
    idade: ProfileField
    perfil: ProfileField
    nivel_poder_compra: ProfileField
    motivacao: ProfileField
    objecao: ProfileField
    comportamento_online: ProfileField
    objetivo_conversao: ProfileField
    fonte_dados_clientes: ProfileField
    fonte_dados_subscritores: ProfileField
    conteudo_a_promover: ProfileField
    objectivo_pos_trafego: ProfileField
    motivacao_clique: ProfileField
    canais: ProfileField
    business_model: ProfileField
    conteudo_forte: bool = False


def pick_best_landing_page(pages: list[PageData]) -> Optional[PageData]:
    for page_type in LANDING_TYPE_PRIORITY:
        match = next((p for p in pages if p.type == page_type), None)
        if match:
            return match
    return pages[0] if pages else None


def _system_prompt() -> str:
    return (
        "És um estratega de aquisição paga. Com base no conteúdo de um site, extrai um perfil "
        "completo para preencher campanhas de Google Ads e Meta Ads. Para cada campo, indica o "
        "valor, a origem (\"site\" se vier directamente do conteúdo do site, \"inferido\" se for "
        "uma dedução razoável a partir do que existe, ou \"default\" se não tiveres qualquer base "
        "e estiveres a usar um valor genérico) e uma confiança entre 0 e 1. Regras: nunca "
        "apresentes um preço que não conste no site - se não houver preço indicado, usa "
        f'"{PRICE_NOT_ON_SITE}" com origin "default". A geografia, se não conseguires inferir do '
        f'site, usa "{DEFAULT_GEOGRAFIA}" com origin "default". Escolhe também business_model '
        '("leads" ou "ecommerce", conforme o site gera contactos/orçamentos ou vende produtos '
        "diretamente) e conteudo_forte (true se o site tiver um blog ou conteúdo editorial forte "
        "que valha a pena promover como tráfego, false caso contrário). " + PT_PT_INSTRUCTION
    )


def _pages_content_block(pages: list[PageData]) -> str:
    lines = []
    for page in pages:
        lines.append(f"--- Página '{page.id}' ({page.type}) {page.url} ---")
        lines.append(f"Title: {page.title}")
        if page.h1:
            lines.append("H1: " + " | ".join(page.h1))
        if page.prices:
            lines.append("Preços mencionados: " + " | ".join(page.prices))
        if page.contacts:
            lines.append("Contactos: " + str(page.contacts))
        lines.append((page.main_text or "")[:1500])
    return "\n".join(lines)


async def extract_profile(llm: LLMClient, pages: list[PageData], *, model: str = "") -> SiteProfile:
    best_landing = pick_best_landing_page(pages)
    prompt = (
        f"{wrap_site_content(_pages_content_block(pages))}\n\n"
        + (f"A página sugerida como melhor landing page é '{best_landing.id}' ({best_landing.url}). " if best_landing else "")
        + "Usa o URL completo dessa página (ou outra mais adequada, se fizer mais sentido) em "
        "landing_page_url e pagina_destino. Devolve APENAS um objecto JSON com um campo por "
        f"cada um destes: {', '.join(PROFILE_FIELDS)}, business_model e conteudo_forte. Cada "
        "campo de perfil (exceto business_model e conteudo_forte) é um objecto {value, origin, "
        "confidence}; business_model é também um objecto {value, origin, confidence} com value "
        'igual a "leads" ou "ecommerce"; conteudo_forte é um boolean simples.'
    )
    profile = await generate_json(llm, task="perfil", system=_system_prompt(), prompt=prompt, schema_model=SiteProfile, model=model)
    return _apply_safety_rules(profile, pages)


def _apply_safety_rules(profile: SiteProfile, pages: list[PageData]) -> SiteProfile:
    """Python-enforced guardrails the LLM must never override (secção 4.5 do briefing)."""
    any_price_on_site = any(page.prices for page in pages)
    if not any_price_on_site and profile.preco.value != PRICE_NOT_ON_SITE:
        profile.preco = ProfileField(value=PRICE_NOT_ON_SITE, origin="default", confidence=1.0)

    if not profile.geografia.value.strip():
        profile.geografia = ProfileField(value=DEFAULT_GEOGRAFIA, origin="default", confidence=0.5)

    known_urls = {page.url for page in pages}
    if profile.landing_page_url.value not in known_urls:
        best_landing = pick_best_landing_page(pages)
        if best_landing:
            profile.landing_page_url = ProfileField(value=best_landing.url, origin="inferido", confidence=0.6)
    if profile.pagina_destino.value not in known_urls:
        profile.pagina_destino = ProfileField(value=profile.landing_page_url.value, origin=profile.landing_page_url.origin, confidence=profile.landing_page_url.confidence)

    if profile.business_model.value not in ("leads", "ecommerce"):
        profile.business_model = ProfileField(value="leads", origin="default", confidence=0.3)

    return profile
