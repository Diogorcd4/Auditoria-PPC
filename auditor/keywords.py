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

# Palavras vazias em PT-PT, ignoradas ao comparar se uma semente/termo "tem a ver" com o site
# (secção A.3): sem isto, qualquer termo com "de"/"para" passaria o teste de relevância.
STOPWORDS_PT = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "para", "com", "por",
    "na", "no", "nas", "nos", "um", "uma", "uns", "umas", "que", "como", "mais", "menos",
    "ao", "aos", "à", "às", "se", "sua", "seu", "suas", "seus", "sem", "sobre", "entre",
}

# Cidades/países/siglas de outros mercados que às vezes aparecem no Autocomplete quando o
# Google interpreta a semente para um mercado errado (secção A.4). Não é uma lista exaustiva -
# só cobre os casos concretos relatados (Espanha, Brasil, LatAm, Angola).
OFF_MARKET_TERMS = {
    "madrid", "barcelona", "sevilla", "valencia", "bilbao", "espanha", "espana", "españa",
    "rj", "sp", "porto alegre", "curitiba", "manaus", "sao paulo", "são paulo", "rio de janeiro",
    "belo horizonte", "brasilia", "brasília", "salvador", "fortaleza", "recife", "lima", "peru",
    "mexico", "méxico", "bogota", "bogotá", "colombia", "colômbia", "angola", "luanda",
    "cnae", "sebrae", "ltda", "reclame aqui",
}
# Palavras claramente em espanhol (não em PT-PT) que às vezes aparecem misturadas no
# Autocomplete quando o Google devolve resultados de outro país de língua próxima.
SPANISH_WORDS = {"camiones", "camión", "envios", "envíos", "precio", "cuanto cuesta", "gratis"}
NOISE_TERMS = {
    "curso", "cursos", "vagas", "vaga", "emprego", "empregos", "estágio", "estagio", "estágios",
    "salário", "salario", "salários", "salarios", "pdf", "grátis", "gratis", "reclame aqui",
    "reclameaqui", "trabalhe conosco", "trabalhe connosco",
}


def _compile_term_boundary_re(terms: set[str]) -> "re.Pattern[str]":
    """Compara por palavra/frase inteira, nunca por substring nua - "sp" (São Paulo) não pode
    apanhar "tranSPorte", nem "rj" apanhar qualquer palavra que o contenha por acaso."""
    escaped = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


OFF_MARKET_RE = _compile_term_boundary_re(OFF_MARKET_TERMS)
SPANISH_WORDS_RE = _compile_term_boundary_re(SPANISH_WORDS)
NOISE_TERMS_RE = _compile_term_boundary_re(NOISE_TERMS)

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


WORD_RE = re.compile(r"[a-zà-öø-ÿ]+", re.IGNORECASE)


def _significant_words(text: str) -> set[str]:
    """Palavras de 3+ letras, sem pontuação e sem stopwords - usadas só para comparar se uma
    semente/termo tem alguma relação com o site (secção A.3), nunca para gerar texto."""
    if not text:
        return set()
    words = WORD_RE.findall(text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS_PT}


def _profile_field_value(profile: Optional[dict], key: str) -> str:
    if not profile:
        return ""
    field = profile.get(key)
    if isinstance(field, dict):
        return field.get("value") or ""
    return ""


def _split_profile_value(value: str) -> list[str]:
    """"Transportes e logística, mudanças" -> ["transportes", "logística", "mudanças"]:
    o Perfil guarda frases, não termos de pesquisa - isto separa-as nos fragmentos que
    _clean_seed depois filtra para 2 a 4 palavras (secção A.2)."""
    return [part for part in re.split(r",|;|\be\b|/", value) if part.strip()]


def _seed_reference_words(pages: list[PageData]) -> set[str]:
    """O vocabulário do próprio site (títulos, H1, meta descrições) - nunca o do Perfil, para
    esta verificação não ser circular (validar uma semente vinda de profile.sector contra as
    palavras desse mesmo profile.sector aceitaria sempre, por definição). É contra ISTO que
    cada semente derivada do Perfil é confrontada (secção A.3)."""
    words: set[str] = set()
    for page in pages:
        words |= _significant_words(page.title)
        words |= _significant_words(page.meta_description)
        for h1 in page.h1:
            words |= _significant_words(h1)
    return words


def _seed_shares_a_significant_word(seed: str, reference_words: set[str]) -> bool:
    return bool(_significant_words(seed) & reference_words) if reference_words else True


def derive_seed_terms_from_profile(profile: dict, *, max_seeds: int = 8) -> list[str]:
    """Fonte primária das sementes (secção A.2): só o setor e o produto/serviço extraídos no
    Perfil - nunca owner_services (que é sobre os serviços que o Auditor vende, não sobre o
    negócio auditado) nem um resumo genérico de página."""
    candidates: "OrderedDict[str, None]" = OrderedDict()
    for key in ("produto_ou_servico", "sector"):
        for fragment in _split_profile_value(_profile_field_value(profile, key)):
            cleaned = _clean_seed(fragment)
            if cleaned:
                candidates.setdefault(cleaned, None)
    return list(candidates.keys())[:max_seeds]


def _derive_seed_terms_from_titles(pages: list[PageData], *, max_seeds: int = 8) -> list[str]:
    """Fallback heurístico (secção A.3): só entra em acção quando o Perfil não existe, ou
    quando sobram menos de 3 sementes válidas vindas dele. H1s e o primeiro segmento do
    título de cada página, filtrados por _clean_seed para nunca incluir frases
    inteiras/slogans nem rótulos de navegação."""
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


def derive_seed_terms(pages: list[PageData], *, profile: Optional[dict] = None, max_seeds: int = 8) -> list[str]:
    """Termos-semente: só podem derivar do Perfil (setor, produto ou serviço) e, como
    fallback, dos títulos/H1 de páginas de serviço/produto do site - nunca de owner_services
    nem de exemplos de outro setor (secção A do pedido de correcção). Cada candidato é
    validado contra o vocabulário do próprio site/Perfil (_seed_shares_a_significant_word);
    os que não partilham nenhuma palavra significativa são descartados e registados no
    terminal, nunca devolvidos como sementes."""
    reference_words = _seed_reference_words(pages)

    def _accept(cleaned: str) -> bool:
        if _seed_shares_a_significant_word(cleaned, reference_words):
            return True
        print(f"[termos] semente descartada (sem relação aparente com o site): '{cleaned}'")
        return False

    seeds: "OrderedDict[str, None]" = OrderedDict()
    if profile:
        for cleaned in derive_seed_terms_from_profile(profile, max_seeds=max_seeds):
            if _accept(cleaned):
                seeds.setdefault(cleaned, None)

    if len(seeds) < 3:
        for cleaned in _derive_seed_terms_from_titles(pages, max_seeds=max_seeds):
            if cleaned in seeds:
                continue
            if _accept(cleaned):
                seeds.setdefault(cleaned, None)
            if len(seeds) >= max_seeds:
                break

    return list(seeds.keys())[:max_seeds]


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


OBSERVED_TERMS_CAP = 60


def _is_off_topic_observed_term(term: str, seed_words: set[str]) -> bool:
    """Um termo observado só é aceite se tocar noutro mercado/idioma/ruído, ou se não tiver
    nenhuma palavra em comum com nenhuma semente (secção A.4 do pedido de correcção)."""
    if OFF_MARKET_RE.search(term) or SPANISH_WORDS_RE.search(term) or NOISE_TERMS_RE.search(term):
        return True
    return not (_significant_words(term) & seed_words)


def filter_observed_terms(terms: list[str], seeds: list[str], *, cap: int = OBSERVED_TERMS_CAP) -> list[str]:
    """Filtra termos observados de outros mercados (cidades/países fora de Portugal, siglas
    brasileiras), espanhol e ruído genérico (curso, vagas, emprego...), mantendo só os que
    partilham uma palavra com alguma semente, até um máximo de `cap` (secção A.4). Regista no
    terminal quantos foram descartados."""
    seed_words: set[str] = set()
    for seed in seeds:
        seed_words |= _significant_words(seed)

    kept: list[str] = []
    discarded = 0
    for term in terms:
        if _is_off_topic_observed_term(term, seed_words) or len(kept) >= cap:
            discarded += 1
            continue
        kept.append(term)

    print(f"[termos] {len(kept)} termos observados mantidos, {discarded} descartados (outro mercado/idioma/ruído ou limite de {cap}).")
    return kept


def _clean_negative_keywords(raw: list[str]) -> list[str]:
    """Cada palavra-chave negativa tem de ser um chip separado (secção A.5): se o modelo
    devolver vários termos colados num único item (por vírgula, ponto e vírgula ou barra),
    isto separa-os antes de chegarem à interface."""
    cleaned: "OrderedDict[str, None]" = OrderedDict()
    for item in raw:
        for fragment in re.split(r"[,;|/]+", item):
            term = fragment.strip().lower()
            if term:
                cleaned.setdefault(term, None)
    return list(cleaned.keys())


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
        "'emprego', 'curso', 'estágio', quando não fizerem sentido para este negócio). Cada "
        "palavra-chave negativa é UM item separado da lista - nunca coloques várias palavras "
        "coladas ou separadas por vírgula dentro do mesmo item. "
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
    profile: Optional[dict] = None,
    city: Optional[str] = None,
    model: str = "",
    cache_dir: Path | str = ".cache/keywords",
    use_alphabet: bool = True,
    delay_seconds: float = 0.3,
    overall_timeout: float = 120.0,
) -> dict:
    """The observed (Google Autocomplete) and inferred (LLM) layers fail independently
    (secção B.6): a blocked/slow Autocomplete never takes down the inferred layer and
    vice-versa. The whole step only raises if BOTH layers come back empty.

    `profile` (o resultado já validado do passo "perfil", que corre antes deste) é a fonte
    primária das sementes - nunca um resumo genérico de página, que é fácil demais de um
    modelo pequeno hallucinate para um setor completamente errado (secção A)."""
    seeds = derive_seed_terms(pages, profile=profile)
    print(f"[termos] sementes: {seeds}")

    raw_observed: list[str] = []
    observed_warning: Optional[str] = None
    try:
        fetched = await asyncio.wait_for(
            fetch_observed_terms(http_client, seeds, city=city, cache_dir=cache_dir, use_alphabet=use_alphabet, delay_seconds=delay_seconds),
            timeout=overall_timeout,
        )
        raw_observed = filter_observed_terms(fetched, seeds)
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
        negatives = _clean_negative_keywords(synthesis.negatives)
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
