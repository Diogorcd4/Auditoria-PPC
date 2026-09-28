from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from auditor.crawler import PageData
from auditor.llm.base import LLMClient
from auditor.llm_json import PT_PT_INSTRUCTION, generate_json, wrap_site_content

CATEGORY_KEYS = [
    "problemas",
    "caracteristicas",
    "motivos_para_comprar",
    "objeccoes_e_receios",
    "desejos",
    "crencas_e_mentalidade",
    "oportunidades",
]

CATEGORY_LABELS = {
    "problemas": "Problemas",
    "caracteristicas": "Características",
    "motivos_para_comprar": "Motivos para comprar",
    "objeccoes_e_receios": "Objecções e receios",
    "desejos": "Desejos",
    "crencas_e_mentalidade": "Crenças e mentalidade",
    "oportunidades": "Oportunidades",
}

LEVEL_SCORE = {"forte": 1.0, "fraco": 0.5, "ausente": 0.0}

PAGE_TYPE_EMPHASIS = {
    "home": "Nesta página (home), dá mais atenção a Problemas, Desejos e Motivos para comprar.",
    "servico": "Nesta página de serviço, dá mais atenção a Características, Objecções e receios e Motivos para comprar.",
    "produto": "Nesta página de produto, dá mais atenção a Características, Objecções e receios e Oportunidades.",
    "sobre": "Nesta página Sobre, dá mais atenção a Crenças e mentalidade e à construção de autoridade.",
    "contacto": "Nesta página de Contacto, foca-te em reduzir fricção e em Objecções e receios.",
}


class CategoryAnalysis(BaseModel):
    level: Literal["forte", "fraco", "ausente", "nao_aplicavel"]
    evidence: str
    recommendation: str


class CTAAnalysis(BaseModel):
    clarity: str
    position: str
    note: str


class PageCommunication(BaseModel):
    page_id: str
    categories: dict[str, CategoryAnalysis]
    cta_analysis: CTAAnalysis

    @field_validator("categories")
    @classmethod
    def _all_categories_present(cls, value: dict) -> dict:
        missing = set(CATEGORY_KEYS) - set(value)
        if missing:
            raise ValueError(f"faltam categorias no JSON: {sorted(missing)}")
        return value


class InsightItem(BaseModel):
    text: str
    source_page: str


class TopImprovement(BaseModel):
    title: str
    impact: Literal["alto", "médio", "baixo"]
    effort: Literal["alto", "médio", "baixo"]
    page: str


class SiteSynthesis(BaseModel):
    insights: dict[str, list[InsightItem]]
    global_gaps: list[str] = Field(default_factory=list)
    message_inconsistencies: list[str] = Field(default_factory=list)
    top_improvements: list[TopImprovement] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    top_opportunities: list[str] = Field(default_factory=list)


def _system_prompt_for_page(page_type: str) -> str:
    base = (
        "És um analista de marketing digital especializado em copywriting e conversão. "
        "Analisa o conteúdo de UMA página de um site e classifica-o nas 7 categorias seguintes: "
        + ", ".join(f"{CATEGORY_LABELS[k]} ({k})" for k in CATEGORY_KEYS)
        + ". Para cada categoria dá um nível (forte, fraco, ausente ou nao_aplicavel, conforme "
        "fizer sentido para este tipo de página), uma evidência curta em paráfrase (nunca "
        "inventada, sempre baseada no texto fornecido) e uma recomendação concreta e acionável "
        "para esta página. Analisa também os CTAs: clareza, posição na página, e se fazem "
        "sentido com a intenção da página.\n\n"
        "Regra mais importante: nunca inventes nada. Se o site não disser algo, o nível é "
        "'ausente' e a evidência deve dizer 'não consta no site'. A evidência e a recomendação "
        "de cada categoria devem ter no máximo 15 palavras cada uma, directas e concretas. "
        + PT_PT_INSTRUCTION
    )
    emphasis = PAGE_TYPE_EMPHASIS.get(page_type)
    return f"{base}\n{emphasis}" if emphasis else base


def _page_content_block(page: PageData, *, max_chars: int = 4000) -> str:
    lines = [
        f"URL: {page.url}",
        f"Tipo de página: {page.type}",
        f"Title: {page.title}",
        f"Meta description: {page.meta_description}",
    ]
    if page.h1:
        lines.append("H1: " + " | ".join(page.h1))
    if page.h2:
        lines.append("H2: " + " | ".join(page.h2))
    if page.ctas:
        lines.append("CTAs: " + " | ".join(page.ctas))
    if page.testimonials:
        lines.append("Testemunhos: " + " | ".join(page.testimonials))
    if page.prices:
        lines.append("Preços mencionados: " + " | ".join(page.prices))
    lines.append("Texto principal: " + ((page.main_text or "(vazio)")[:max_chars]))
    return "\n".join(lines)


async def analyze_page(llm: LLMClient, page: PageData, *, model: str = "", max_chars: int = 4000) -> PageCommunication:
    system = _system_prompt_for_page(page.type)
    prompt = (
        f"{wrap_site_content(_page_content_block(page, max_chars=max_chars))}\n\n"
        "Devolve APENAS um objecto JSON com os campos: page_id (usa exactamente "
        f'"{page.id}"), categories (um objecto com uma chave por categoria - '
        f"{', '.join(CATEGORY_KEYS)} - cada uma com level/evidence/recommendation), e "
        "cta_analysis (clarity/position/note)."
    )
    result = await generate_json(llm, task="analise", system=system, prompt=prompt, schema_model=PageCommunication, model=model)
    if result.page_id != page.id:
        result = result.model_copy(update={"page_id": page.id})
    return result


def compute_communication_score(pages: list[PageCommunication]) -> int:
    """Computed in Python, not by the LLM (secção 4.3): forte=1, fraco=0.5, ausente=0,
    averaged over every applicable (not nao_aplicavel) category across every page."""
    scores = [LEVEL_SCORE[cat.level] for page in pages for cat in page.categories.values() if cat.level in LEVEL_SCORE]
    if not scores:
        return 0
    return round(sum(scores) / len(scores) * 100)


def _synthesis_system_prompt() -> str:
    return (
        "És um estratega de marketing digital. Recebes um resumo já feito, página a página, das "
        "7 categorias de comunicação de um site (Problemas, Características, Motivos para "
        "comprar, Objecções e receios, Desejos, Crenças e mentalidade, Oportunidades). A tua "
        "tarefa é sintetizar isto a nível do site inteiro: um mapa de insights (só com o que o "
        "site já suporta, cada ponto citando a página de origem), lacunas globais (temas que "
        "nenhuma página aborda), incoerências de mensagem entre páginas, e um top 10 de "
        "melhorias priorizadas por impacto e esforço, cada uma associada à página onde se aplica. "
        "Resume ainda tudo isto em três listas curtas para um resumo executivo: strengths (até 3 "
        "pontos fortes), weaknesses (até 3 pontos fracos) e top_opportunities (até 3 das maiores "
        "oportunidades). " + PT_PT_INSTRUCTION
    )


def _synthesis_content_block(pages: list[PageData], communications: list[PageCommunication]) -> str:
    pages_by_id = {p.id: p for p in pages}
    lines = []
    for comm in communications:
        page = pages_by_id.get(comm.page_id)
        lines.append(f"- Página '{comm.page_id}' ({page.type if page else '?'}, {page.url if page else ''}):")
        for key in CATEGORY_KEYS:
            cat = comm.categories[key]
            lines.append(f"    {CATEGORY_LABELS[key]}: {cat.level} — {cat.evidence}")
    return "\n".join(lines)


async def synthesize_site(
    llm: LLMClient,
    pages: list[PageData],
    communications: list[PageCommunication],
    *,
    model: str = "",
) -> SiteSynthesis:
    page_ids = {p.id for p in pages}
    prompt = (
        f"{wrap_site_content(_synthesis_content_block(pages, communications))}\n\n"
        f"Os IDs de página válidos são: {', '.join(sorted(page_ids))}. Usa sempre um destes IDs "
        "em source_page/page, nunca um URL ou um nome inventado. Devolve APENAS um objecto JSON "
        "com os campos: insights (objecto com uma chave por categoria - "
        f"{', '.join(CATEGORY_KEYS)} - cada uma uma lista de objectos {{text, source_page}}), "
        "global_gaps (lista de strings), message_inconsistencies (lista de strings), "
        "top_improvements (lista de até 10 objectos com title, impact, effort, page), "
        "strengths (lista de até 3 strings), weaknesses (lista de até 3 strings), "
        "top_opportunities (lista de até 3 strings)."
    )
    result = await generate_json(llm, task="sintese", system=_synthesis_system_prompt(), prompt=prompt, schema_model=SiteSynthesis, model=model)

    # Defensive: an LLM occasionally cites a page that doesn't exist - drop those rather than
    # let a broken reference reach the report.
    result.insights = {key: [item for item in items if item.source_page in page_ids] for key, items in result.insights.items()}
    result.top_improvements = [t for t in result.top_improvements if t.page in page_ids]
    return result


async def analyze_communication(llm: LLMClient, pages: list[PageData], *, model: str = "") -> tuple[list[PageCommunication], SiteSynthesis, int]:
    """Run the per-page analysis for every page, then the site-level synthesis, then compute
    the communication score in Python. This is the whole "comunicacao" + "sintese" pipeline step."""
    communications = [await analyze_page(llm, page, model=model) for page in pages]
    synthesis = await synthesize_site(llm, pages, communications, model=model)
    score = compute_communication_score(communications)
    return communications, synthesis, score
