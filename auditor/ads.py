from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from auditor.crawler import PageData
from auditor.llm.base import LLMClient
from auditor.llm_json import PT_PT_INSTRUCTION, LLMJsonError, generate_json, wrap_site_content
from auditor.prompts import fill_template
from auditor.validators import AssetCheck, SitelinkCheck, load_validation_config, validate_group, validate_primary_text, validate_sitelinks

TEMPLATE_META: dict[str, dict[str, str]] = {
    "02.1_search_leads.md": {"key": "02.1", "platform": "Google Search Ads", "label": "Search · Leads"},
    "02.2_pmax_leads.md": {"key": "02.2", "platform": "Google Performance Max", "label": "pMax · Leads"},
    "02.3_search_ecommerce.md": {"key": "02.3", "platform": "Google Search Ads", "label": "Search · Ecommerce"},
    "02.4_pmax_ecommerce.md": {"key": "02.4", "platform": "Google Performance Max", "label": "pMax · Ecommerce"},
    "02.5_meta_leads.md": {"key": "02.5", "platform": "Meta Ads", "label": "Meta · Leads"},
    "02.6_meta_ecommerce.md": {"key": "02.6", "platform": "Meta Ads", "label": "Meta · Ecommerce"},
    "02.7_meta_trafego.md": {"key": "02.7", "platform": "Meta Ads", "label": "Meta · Tráfego"},
}

TECH_NOTE = (
    "NOTA TÉCNICA: não tens acesso à web nem à execução de código nesta sessão (ignora "
    "qualquer instrução do prompt acima para usares Python). Conta os caracteres de cada "
    "texto com rigor, porque a validação dos limites é feita externamente, fora desta "
    "conversa. No fim, devolve um bloco JSON entre <<<JSON e JSON>>> com os assets de texto "
    "pedidos, e nada mais além desse bloco."
)

MAX_CORRECTION_ROUNDS = 5


class TextBatch(BaseModel):
    items: list[str] = Field(default_factory=list)


class TextFix(BaseModel):
    text: str


class SitelinkItem(BaseModel):
    text: str
    url: str = ""
    descriptions: list[str]


class SitelinkBatch(BaseModel):
    sitelinks: list[SitelinkItem] = Field(default_factory=list)


def flatten_profile(profile: dict[str, Any]) -> dict[str, str]:
    """SiteProfile.model_dump() wraps every field as {value, origin, confidence}; the
    template filler just wants the plain value per field."""
    return {key: entry["value"] for key, entry in profile.items() if isinstance(entry, dict) and "value" in entry}


def _select_relevant_pages(pages: list[PageData], landing_url: str, *, max_chars: int = 4000) -> list[PageData]:
    landing = next((p for p in pages if p.url == landing_url), None)
    home = next((p for p in pages if p.type == "home"), None)
    ordered = [p for p in (landing, home) if p is not None]
    # dedupe while preserving order
    seen_ids = set()
    unique = []
    for p in ordered:
        if p.id not in seen_ids:
            unique.append(p)
            seen_ids.add(p.id)
    if not unique and pages:
        unique = [pages[0]]

    budget = max_chars
    trimmed = []
    for page in unique:
        text = page.main_text[:budget]
        trimmed.append((page, text))
        budget -= len(text)
        if budget <= 0:
            break
    return trimmed  # list[tuple[PageData, str]]


def _grounding_block(verified_offers: list[dict[str, Any]], pages: list[PageData]) -> str:
    """Regras e factos que os anúncios só podem afirmar (secção E.1/E.2 do pedido de
    correcção): sem isto, um modelo tende a preencher lacunas com superlativos genéricos
    ("líder", "melhor", "garantido") que o site nunca disse."""
    if verified_offers:
        offers_lines = "\n".join(f'- {o["claim"]} (fonte: {o["url"]} - "{o["quote"]}")' for o in verified_offers)
    else:
        offers_lines = "(nenhuma oferta verificada foi extraída do site - não afirmes nenhum número, prazo ou garantia)"
    known_urls = "\n".join(f"- {p.url}" for p in pages)
    return (
        "OFERTAS VERIFICADAS (a única base factual que podes usar):\n"
        f"{offers_lines}\n\n"
        "REGRAS OBRIGATÓRIAS: só podes afirmar uma promessa, número, prazo ou garantia se "
        "constar literalmente na lista de ofertas verificadas acima. Nunca uses superlativos "
        "ou promessas sem base como \"líder\", \"melhor\", \"número 1\", \"garantido\" ou \"em "
        "tempo real\", a não ser que apareçam literalmente numa citação acima. Se gerares um "
        "sitelink, o seu URL tem de ser exactamente um destes URLs do site rastreado:\n"
        f"{known_urls}"
    )


def build_full_prompt(template_name: str, profile_flat: dict[str, str], pages: list[PageData], *, verified_offers: Optional[list[dict[str, Any]]] = None) -> tuple[str, list[str]]:
    filled, missing = fill_template(template_name, profile_flat)
    relevant = _select_relevant_pages(pages, profile_flat.get("landing_page_url", ""))
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content_block = "\n\n".join(f"--- {page.url} ---\n{text}" for page, text in relevant)
    full_prompt = (
        f"{filled}\n\n"
        f"CONTEÚDO REAL DO SITE (recolhido automaticamente em {date_str})\n"
        f"{wrap_site_content(content_block)}\n\n"
        f"{_grounding_block(verified_offers or [], pages)}\n\n"
        f"{TECH_NOTE}"
    )
    return full_prompt, missing


async def _generate_text_batch(llm: LLMClient, *, task: str, context: str, count: int, kind: str, already: list[str], model: str) -> list[str]:
    already_note = f" Já foram geradas estas, não as repitas: {already}." if already else ""
    system = f"Gera exactamente {count} {kind} para esta campanha de anúncios, seguindo as regras do prompt.{already_note} " + PT_PT_INSTRUCTION
    prompt = f"{context}\n\nDevolve APENAS um objecto JSON {{\"items\": [...]}} com exactamente {count} strings."
    result = await generate_json(llm, task=task, system=system, prompt=prompt, schema_model=TextBatch, model=model)
    return result.items[:count]


async def _generate_batched_texts(llm: LLMClient, *, task: str, context: str, n: int, batch_size: int, kind: str, model: str) -> list[str]:
    texts: list[str] = []
    # A weak local model can return fewer items than asked, or nothing at all; cap attempts
    # so a misbehaving LLM degrades to "fewer items than requested" instead of hanging forever.
    max_attempts = max(3, -(-n // max(batch_size, 1)) * 3)
    for _ in range(max_attempts):
        if len(texts) >= n:
            break
        take = min(batch_size, n - len(texts))
        batch = await _generate_text_batch(llm, task=task, context=context, count=take, kind=kind, already=texts, model=model)
        texts.extend(batch)
    return texts[:n]


def _issue_summary(check: AssetCheck) -> str:
    return "; ".join(check.issues) if check.issues else "sem problemas"


async def _correct_text_item(llm: LLMClient, *, task: str, context: str, original: str, issues: list[str], model: str) -> str:
    system = "Corrige o texto de anúncio abaixo para deixar de violar as regras indicadas, mudando o mínimo possível. Conta os caracteres com rigor. " + PT_PT_INSTRUCTION
    prompt = (
        f"{context}\n\nTexto original ({len(original)} caracteres): \"{original}\"\n"
        f"Problemas a corrigir: {'; '.join(issues)}\n"
        "Devolve APENAS um objecto JSON {\"text\": \"...\"} com o texto corrigido."
    )
    result = await generate_json(llm, task=task, system=system, prompt=prompt, schema_model=TextFix, model=model)
    return result.text


async def _run_correction_rounds(
    llm: LLMClient,
    *,
    task: str,
    context: str,
    texts: list[str],
    validate_kwargs: dict,
    max_rounds: int,
    model: str,
) -> list[AssetCheck]:
    checks = validate_group(texts, **validate_kwargs)
    for _ in range(max_rounds):
        failed = [i for i, c in enumerate(checks) if not c.valid]
        if not failed:
            break
        for i in failed:
            try:
                texts[i] = await _correct_text_item(llm, task=task, context=context, original=checks[i].text, issues=checks[i].issues, model=model)
            except LLMJsonError:
                pass  # this item stays as-is; later rounds (or giving up) still apply to it
        checks = validate_group(texts, **validate_kwargs)
    return checks


async def _fix_one_sitelink(llm: LLMClient, *, task: str, context: str, original: dict, issues: list[str], model: str) -> dict:
    system = "Corrige este sitelink (texto + URL + descrições) para deixar de violar as regras indicadas, mudando o mínimo possível. O URL tem de ser um dos URLs do site rastreado indicados no contexto. Conta os caracteres com rigor. " + PT_PT_INSTRUCTION
    prompt = (
        f"{context}\n\nSitelink original: {original}\n"
        f"Problemas a corrigir: {'; '.join(issues)}\n"
        "Devolve APENAS um objecto JSON {\"text\": \"...\", \"url\": \"...\", \"descriptions\": [...]} com o sitelink corrigido."
    )
    result = await generate_json(llm, task=task, system=system, prompt=prompt, schema_model=SitelinkItem, model=model)
    return {"text": result.text, "url": result.url or original.get("url", ""), "descriptions": result.descriptions}


async def _generate_sitelinks(
    llm: LLMClient,
    *,
    task: str,
    context: str,
    n: int,
    desc_n: int,
    text_max: int,
    desc_max: int,
    desc_ends_with_period: bool,
    max_rounds: int,
    model: str,
    verified_quotes: Optional[list[str]] = None,
    known_urls: Optional[set[str]] = None,
) -> list[SitelinkCheck]:
    system = f"Gera exactamente {n} sitelinks para esta campanha, cada um com um texto curto, um URL (exactamente um dos URLs do site rastreado indicados no contexto) e exactamente {desc_n} descriptions. " + PT_PT_INSTRUCTION
    prompt = f"{context}\n\nDevolve APENAS um objecto JSON {{\"sitelinks\": [{{\"text\": \"...\", \"url\": \"...\", \"descriptions\": [...]}}]}} com exactamente {n} sitelinks, cada um com exactamente {desc_n} descriptions."
    result = await generate_json(llm, task=task, system=system, prompt=prompt, schema_model=SitelinkBatch, model=model)
    sitelinks = [{"text": sl.text, "url": sl.url, "descriptions": sl.descriptions[:desc_n]} for sl in result.sitelinks[:n]]

    def _validate() -> list[SitelinkCheck]:
        return validate_sitelinks(
            sitelinks,
            text_max=text_max,
            desc_max=desc_max,
            desc_ends_with_period=desc_ends_with_period,
            verified_quotes=verified_quotes,
            known_urls=known_urls,
        )

    checks = _validate()
    for _ in range(max_rounds):
        bad = [i for i, c in enumerate(checks) if not c.text.valid or any(not d.valid for d in c.descriptions)]
        if not bad:
            break
        for i in bad:
            issues = list(checks[i].text.issues)
            for d in checks[i].descriptions:
                issues.extend(d.issues)
            try:
                sitelinks[i] = await _fix_one_sitelink(llm, task=task, context=context, original=sitelinks[i], issues=issues, model=model)
            except LLMJsonError:
                pass
        checks = _validate()
    return checks


async def generate_template_ads(
    llm: LLMClient,
    template_name: str,
    profile: dict[str, Any],
    pages: list[PageData],
    *,
    model: str = "",
    validation_config: Optional[dict] = None,
    max_correction_rounds: int = MAX_CORRECTION_ROUNDS,
    prompts_output_dir: Optional[Path] = None,
    verified_offers: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Fill one template, run its batched generation + up to `max_correction_rounds`
    correction rounds per group, and return it in the shape the frontend/report expect."""
    verified_offers = verified_offers or []
    verified_quotes = [o["quote"] for o in verified_offers]
    known_urls = {p.url for p in pages}
    profile_flat = flatten_profile(profile)
    full_prompt, missing_placeholders = build_full_prompt(template_name, profile_flat, pages, verified_offers=verified_offers)

    if prompts_output_dir is not None:
        prompts_output_dir.mkdir(parents=True, exist_ok=True)
        (prompts_output_dir / template_name).write_text(full_prompt, encoding="utf-8")

    meta = TEMPLATE_META[template_name]
    key = meta["key"]
    config = validation_config or load_validation_config()
    rules = config[key]
    title_case_mode = config.get("title_case_mode", "all_words")

    block: dict[str, Any] = {"template": template_name, "platform": meta["platform"], "label": meta["label"]}
    if missing_placeholders:
        block["missing_placeholders"] = missing_placeholders

    if "primary_text" in rules:
        r = rules["primary_text"]
        texts = await _generate_batched_texts(llm, task=f"anuncios_{key}_primary_text", context=full_prompt, n=r["n"], batch_size=r["n"], kind="Primary Text", model=model)
        block["primary_text"] = [c.model_dump() for c in validate_primary_text(texts, preview=r.get("preview", 125), verified_quotes=verified_quotes)]

    if "headlines" in rules:
        r = rules["headlines"]
        texts = await _generate_batched_texts(llm, task=f"anuncios_{key}_headlines", context=full_prompt, n=r["n"], batch_size=5, kind="headlines", model=model)
        checks = await _run_correction_rounds(
            llm,
            task=f"anuncios_{key}_headlines_fix",
            context=full_prompt,
            texts=texts,
            validate_kwargs={
                "max_len": r.get("max"),
                "min_len": r.get("min"),
                "title_case": r.get("title_case", False),
                "title_case_mode": title_case_mode,
                "forbidden_words": r.get("forbidden_words"),
                "unique": r.get("unique", False),
                "verified_quotes": verified_quotes,
            },
            max_rounds=max_correction_rounds,
            model=model,
        )
        block["headlines"] = [c.model_dump() for c in checks]

    if "long_headlines" in rules:
        r = rules["long_headlines"]
        texts = await _generate_batched_texts(llm, task=f"anuncios_{key}_long_headlines", context=full_prompt, n=r["n"], batch_size=r["n"], kind="long headlines", model=model)
        checks = await _run_correction_rounds(
            llm,
            task=f"anuncios_{key}_long_headlines_fix",
            context=full_prompt,
            texts=texts,
            validate_kwargs={"min_len": r.get("min"), "max_len": r.get("max"), "verified_quotes": verified_quotes},
            max_rounds=max_correction_rounds,
            model=model,
        )
        block["long_headlines"] = [c.model_dump() for c in checks]

    if "descriptions" in rules:
        r = rules["descriptions"]
        texts = await _generate_batched_texts(llm, task=f"anuncios_{key}_descriptions", context=full_prompt, n=r["n"], batch_size=r["n"], kind="descriptions", model=model)
        checks = await _run_correction_rounds(
            llm,
            task=f"anuncios_{key}_descriptions_fix",
            context=full_prompt,
            texts=texts,
            validate_kwargs={"min_len": r.get("min"), "max_len": r.get("max"), "verified_quotes": verified_quotes},
            max_rounds=max_correction_rounds,
            model=model,
        )
        block["descriptions"] = [c.model_dump() for c in checks]

    if "sitelinks" in rules:
        r = rules["sitelinks"]
        sitelink_checks = await _generate_sitelinks(
            llm,
            task=f"anuncios_{key}_sitelinks",
            context=full_prompt,
            n=r["n"],
            desc_n=r["desc_n"],
            text_max=r["text_max"],
            desc_max=r["desc_max"],
            desc_ends_with_period=r.get("desc_ends_with_period", False),
            max_rounds=max_correction_rounds,
            model=model,
            verified_quotes=verified_quotes,
            known_urls=known_urls,
        )
        block["sitelinks"] = [c.model_dump() for c in sitelink_checks]

    return block


async def generate_all_ads(
    llm: LLMClient,
    template_names: list[str],
    profile: dict[str, Any],
    pages: list[PageData],
    *,
    model: str = "",
    max_correction_rounds: int = MAX_CORRECTION_ROUNDS,
    prompts_output_dir: Optional[Path] = None,
    verified_offers: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    config = load_validation_config()
    ads: dict[str, Any] = {}
    for template_name in template_names:
        key = TEMPLATE_META[template_name]["key"]
        ads[key] = await generate_template_ads(
            llm,
            template_name,
            profile,
            pages,
            model=model,
            validation_config=config,
            max_correction_rounds=max_correction_rounds,
            prompts_output_dir=prompts_output_dir,
            verified_offers=verified_offers,
        )
    return ads
