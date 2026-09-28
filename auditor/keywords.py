from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from auditor.crawler import PageData
from auditor.llm.base import LLMClient
from auditor.llm_json import PT_PT_INSTRUCTION, generate_json, wrap_site_content

ALPHABET = list("abcdefghijklmnopqrstuvwxyz")
KEYWORD_SUFFIXES = ["preço", "quanto custa", "melhor", "perto de mim", "opiniões"]

STAGE_KEYWORDS = {
    "decisao": ["preço", "quanto custa", "orçamento", "financiamento", "marcar", "agendar", "comprar"],
    "comparacao": ["melhor", " vs ", "versus", "comparar", "opiniões", "reviews", "avaliações"],
    "transacional_local": ["perto de mim", "perto", "em lisboa", "no porto", "ao meu lado"],
    "problema": ["o que é", "porque", "como", "sintomas", "problema", "dói"],
}

NOTE_TEXT = (
    "Sem dados de volume de pesquisa nesta auditoria; validar as prioridades no Keyword "
    "Planner antes de orçamentar campanhas."
)


def heuristic_stage(term: str) -> str:
    lower = term.lower()
    for stage, keywords in STAGE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return stage
    return "solucao"


def derive_seed_terms(pages: list[PageData], *, max_seeds: int = 8) -> list[str]:
    candidates: "OrderedDict[str, None]" = OrderedDict()

    home = next((p for p in pages if p.type == "home"), pages[0] if pages else None)
    if home and home.h1:
        candidates.setdefault(home.h1[0].lower(), None)

    for page in pages:
        if page.type in ("servico", "produto", "categoria") and page.h1:
            candidates.setdefault(page.h1[0].lower(), None)

    for page in pages:
        if page.title:
            first_segment = page.title.split(" — ")[0].split(" | ")[0].split(" - ")[0].strip().lower()
            if first_segment:
                candidates.setdefault(first_segment, None)

    return list(candidates.keys())[:max_seeds]


async def fetch_autocomplete(client: httpx.AsyncClient, query: str, *, hl: str = "pt-PT", gl: str = "pt") -> list[str]:
    try:
        resp = await client.get(
            "https://www.google.com/complete/search",
            params={"client": "firefox", "hl": hl, "gl": gl, "q": query},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return [s for s in data[1]] if len(data) > 1 else []
    except (httpx.HTTPError, ValueError, IndexError, KeyError):
        return []


async def fetch_observed_terms(
    client: httpx.AsyncClient,
    seeds: list[str],
    *,
    city: Optional[str] = None,
    delay_seconds: float = 0.3,
    cache_dir: Path | str = ".cache/keywords",
    use_alphabet: bool = True,
) -> list[str]:
    """Google Autocomplete, expanded per seed with the suffixes and the a-z sweep the
    briefing asks for (secção 4.4). Every query is cached to disk so a repeated run doesn't
    re-hit the network, and there's a pause between requests."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    results: "OrderedDict[str, None]" = OrderedDict()

    for seed in seeds:
        suffix_queries = [f"{seed} {s}" for s in KEYWORD_SUFFIXES]
        if city:
            suffix_queries.append(f"{seed} {city}")
        alphabet_queries = [f"{seed} {letter}" for letter in ALPHABET] if use_alphabet else []
        queries = [seed] + alphabet_queries + suffix_queries

        for query in queries:
            cache_file = cache_path / f"{hashlib.sha1(query.encode()).hexdigest()}.json"
            if cache_file.exists():
                suggestions = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                suggestions = await fetch_autocomplete(client, query)
                cache_file.write_text(json.dumps(suggestions, ensure_ascii=False), encoding="utf-8")
                if delay_seconds:
                    await asyncio.sleep(delay_seconds)
            for suggestion in suggestions:
                results.setdefault(suggestion, None)

    return list(results.keys())


class InferredKeywords(BaseModel):
    problema: list[str] = Field(default_factory=list)
    solucao: list[str] = Field(default_factory=list)
    comparacao: list[str] = Field(default_factory=list)
    decisao: list[str] = Field(default_factory=list)
    transacional_local: list[str] = Field(default_factory=list)


class KeywordsSynthesis(BaseModel):
    inferred: InferredKeywords
    gaps: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)


def _synthesis_system_prompt() -> str:
    return (
        "És um especialista em SEO e Google Ads. Recebes termos-semente derivados de um site, "
        "termos observados no Google Autocomplete, e um resumo das páginas do site. A tua "
        "tarefa é: 1) agrupar e completar os termos de pesquisa relevantes em 5 etapas de "
        "intenção (problema, solucao, comparacao, decisao, transacional_local), destacando "
        "sobretudo os termos de quem já compara soluções e está perto de contratar ou comprar; "
        "2) identificar lacunas de conteúdo: temas relevantes para este negócio que não têm "
        "nenhuma página correspondente no site; 3) sugerir palavras-chave negativas para excluir "
        "de campanhas de Google Ads (termos que atraem cliques não qualificados, como 'grátis', "
        "'emprego', 'curso', 'estágio', quando não fizerem sentido para este negócio). "
        + PT_PT_INSTRUCTION
    )


def _pages_summary(pages: list[PageData]) -> str:
    return "\n".join(f"- {p.type}: {p.title or p.url}" for p in pages)


async def infer_keywords_synthesis(
    llm: LLMClient,
    *,
    seeds: list[str],
    observed: list[str],
    pages: list[PageData],
    model: str = "",
) -> KeywordsSynthesis:
    content = (
        f"Termos-semente: {', '.join(seeds)}\n"
        f"Termos observados no Autocomplete: {', '.join(observed[:60])}\n"
        f"Páginas do site:\n{_pages_summary(pages)}"
    )
    prompt = (
        f"{wrap_site_content(content)}\n\n"
        "Devolve APENAS um objecto JSON com os campos: inferred (objecto com as chaves "
        "problema, solucao, comparacao, decisao, transacional_local, cada uma uma lista de "
        "termos), gaps (lista de strings) e negatives (lista de strings)."
    )
    return await generate_json(llm, task="keywords", system=_synthesis_system_prompt(), prompt=prompt, schema_model=KeywordsSynthesis, model=model)


async def build_keywords(
    llm: LLMClient,
    http_client: httpx.AsyncClient,
    pages: list[PageData],
    *,
    city: Optional[str] = None,
    model: str = "",
    cache_dir: Path | str = ".cache/keywords",
    use_alphabet: bool = True,
    delay_seconds: float = 0.3,
) -> dict:
    seeds = derive_seed_terms(pages)
    raw_observed = await fetch_observed_terms(http_client, seeds, city=city, cache_dir=cache_dir, use_alphabet=use_alphabet, delay_seconds=delay_seconds)
    synthesis = await infer_keywords_synthesis(llm, seeds=seeds, observed=raw_observed, pages=pages, model=model)

    return {
        "observed": [{"term": term, "stage": heuristic_stage(term)} for term in raw_observed],
        "inferred": synthesis.inferred.model_dump(),
        "gaps": synthesis.gaps,
        "negatives": synthesis.negatives,
        "note": NOTE_TEXT,
    }
