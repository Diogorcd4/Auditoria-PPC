from __future__ import annotations

import asyncio
import hashlib
import json
import re
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
# Só as 3 melhores sementes recebem a barredura a-z completa (26 pedidos cada); as restantes
# recebem só os sufixos - o a-z em todas as sementes é o que gerava centenas de termos de
# baixa qualidade (secção 8 do pedido de correcção).
ALPHABET_TOP_N_SEEDS = 3

# Rótulos de menu/navegação que nunca são termos de pesquisa reais, mesmo quando sobrevivem ao
# filtro de 2 a 4 palavras (ex.: "sobre mim", "quem somos").
NAV_STOPWORDS = {
    "sobre mim", "quem somos", "fale connosco", "entre em contacto", "a nossa equipa",
    "termos e condicoes", "termos e condições", "politica de privacidade", "política de privacidade",
}

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


def _clean_seed(text: Optional[str]) -> Optional[str]:
    """Só aceita frases curtas de pesquisa (2 a 4 palavras, sem pontuação final) - nunca um
    H1/título inteiro (slogan, frase completa) nem um rótulo de menu de navegação isolado
    (secção 8 do pedido de correcção)."""
    if not text:
        return None
    cleaned = text.strip().strip(".,!?;:—–-").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).lower()
    if not cleaned or len(cleaned) > 40:
        return None
    if cleaned in NAV_STOPWORDS:
        return None
    word_count = len(cleaned.split())
    if word_count < 2 or word_count > 4:
        return None
    return cleaned


def derive_seed_terms(pages: list[PageData], *, max_seeds: int = 8) -> list[str]:
    """Fallback heurístico, só usado quando infer_seed_terms (via IA) falha ou não devolve
    nada (secção 8): H1s e o primeiro segmento do título de cada página, filtrados por
    _clean_seed para nunca incluir frases inteiras/slogans nem rótulos de navegação."""
    candidates: "OrderedDict[str, None]" = OrderedDict()

    home = next((p for p in pages if p.type == "home"), pages[0] if pages else None)
    if home and home.h1:
        cleaned = _clean_seed(home.h1[0])
        if cleaned:
            candidates.setdefault(cleaned, None)

    for page in pages:
        if page.type in ("servico", "produto", "categoria") and page.h1:
            cleaned = _clean_seed(page.h1[0])
            if cleaned:
                candidates.setdefault(cleaned, None)

    for page in pages:
        if page.title:
            first_segment = page.title.split(" — ")[0].split(" | ")[0].split(" - ")[0]
            cleaned = _clean_seed(first_segment)
            if cleaned:
                candidates.setdefault(cleaned, None)

    return list(candidates.keys())[:max_seeds]


async def fetch_autocomplete(client: httpx.AsyncClient, query: str, *, hl: str = "pt-PT", gl: str = "pt", timeout: float = 10.0) -> list[str]:
    try:
        resp = await client.get(
            "https://www.google.com/complete/search",
            params={"client": "firefox", "hl": hl, "gl": gl, "q": query},
            timeout=timeout,
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
    alphabet_top_n: int = ALPHABET_TOP_N_SEEDS,
) -> list[str]:
    """Google Autocomplete, expanded per seed with the suffixes for every seed, and the a-z
    sweep only for the top `alphabet_top_n` seeds (secção 8: a barredura completa em todas as
    sementes gerava centenas de termos de baixa qualidade). Every query is cached to disk so
    a repeated run doesn't re-hit the network, and there's a pause between requests."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    results: "OrderedDict[str, None]" = OrderedDict()

    for index, seed in enumerate(seeds):
        suffix_queries = [f"{seed} {s}" for s in KEYWORD_SUFFIXES]
        if city:
            suffix_queries.append(f"{seed} {city}")
        alphabet_queries = [f"{seed} {letter}" for letter in ALPHABET] if use_alphabet and index < alphabet_top_n else []
        queries = [seed] + alphabet_queries + suffix_queries

        for query in queries:
            cache_file = cache_path / f"{hashlib.sha1(query.encode()).hexdigest()}.json"
            if cache_file.exists():
                suggestions = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                print(f"[termos] pedido Autocomplete: '{query}'")
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


class SeedTerms(BaseModel):
    seeds: list[str] = Field(default_factory=list)


def _seed_system_prompt() -> str:
    return (
        "És um especialista em SEO e Google Ads. A partir de um resumo das páginas de um site, "
        "sugere entre 5 e 8 termos-semente de pesquisa para este negócio. Cada termo tem de ser "
        "uma frase curta de pesquisa, com 2 a 4 palavras, sem pontuação final. Nunca uses uma "
        "frase completa, um slogan, um título/headline inteiro, nem um rótulo de menu de "
        "navegação isolado (por exemplo, nunca 'início', 'serviços', 'sobre nós', 'contacto' ou "
        "'blog' sozinhos). Pensa em como um cliente pesquisaria no Google para encontrar este "
        "tipo de negócio. " + PT_PT_INSTRUCTION
    )


async def infer_seed_terms(llm: LLMClient, pages: list[PageData], *, model: str = "") -> list[str]:
    """Pede ao modelo 5 a 8 termos-semente a partir do resumo do site (secção 8): é a fonte
    primária de sementes, mais fiável do que a heurística de H1s/títulos (que pode acabar por
    usar slogans inteiros ou rótulos de navegação). derive_seed_terms só entra em acção se
    isto falhar ou devolver uma lista vazia."""
    prompt = (
        f"{wrap_site_content(_pages_summary(pages))}\n\n"
        "Devolve APENAS um objecto JSON com o campo: seeds (lista de 5 a 8 termos-semente)."
    )
    result = await generate_json(llm, task="keywords", system=_seed_system_prompt(), prompt=prompt, schema_model=SeedTerms, model=model)
    return result.seeds


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
    overall_timeout: float = 120.0,
) -> dict:
    """The observed (Google Autocomplete) and inferred (LLM) layers fail independently
    (secção B.6): a blocked/slow Autocomplete never takes down the inferred layer and
    vice-versa. The whole step only raises if BOTH layers come back empty."""
    seeds: list[str] = []
    try:
        raw_seeds = await infer_seed_terms(llm, pages, model=model)
        seeds = list(OrderedDict.fromkeys(cleaned for s in raw_seeds if (cleaned := _clean_seed(s))))
    except Exception as exc:  # noqa: BLE001 - falls back to the H1/title heuristic below
        print(f"[termos] não foi possível inferir sementes com IA, a usar heurística de H1/título: {exc}")
    if not seeds:
        seeds = derive_seed_terms(pages)
    print(f"[termos] sementes: {seeds}")

    raw_observed: list[str] = []
    observed_warning: Optional[str] = None
    try:
        raw_observed = await asyncio.wait_for(
            fetch_observed_terms(http_client, seeds, city=city, cache_dir=cache_dir, use_alphabet=use_alphabet, delay_seconds=delay_seconds),
            timeout=overall_timeout,
        )
        print(f"[termos] {len(raw_observed)} termos observados (Google Autocomplete).")
    except asyncio.TimeoutError:
        observed_warning = f"A pesquisa de termos observados excedeu {overall_timeout:.0f}s e foi interrompida; a continuar só com termos inferidos."
        print(f"[termos] {observed_warning}")
    except Exception as exc:  # noqa: BLE001 - autocomplete is best-effort, never fatal on its own
        observed_warning = f"Não foi possível obter termos observados (Google Autocomplete): {exc}"
        print(f"[termos] {observed_warning}")

    print("[termos] a pedir a síntese inferida ao modelo…")
    inferred: dict[str, list[str]] = {key: [] for key in InferredKeywords.model_fields}
    gaps: list[str] = []
    negatives: list[str] = []
    inferred_warning: Optional[str] = None
    try:
        synthesis = await infer_keywords_synthesis(llm, seeds=seeds, observed=raw_observed, pages=pages, model=model)
        inferred = synthesis.inferred.model_dump()
        gaps = synthesis.gaps
        negatives = synthesis.negatives
        print("[termos] síntese concluída.")
    except Exception as exc:  # noqa: BLE001 - fall back to the observed layer alone
        inferred_warning = f"Não foi possível agrupar os termos com IA: {exc}"
        print(f"[termos] {inferred_warning}")

    warnings = [w for w in (observed_warning, inferred_warning) if w]
    if not raw_observed and not any(inferred.values()):
        raise RuntimeError("Não foi possível obter termos observados nem inferidos. " + " ".join(warnings))

    result = {
        "observed": [{"term": term, "stage": heuristic_stage(term)} for term in raw_observed],
        "inferred": inferred,
        "gaps": gaps,
        "negatives": negatives,
        "note": NOTE_TEXT,
    }
    if warnings:
        result["warnings"] = warnings
    return result
