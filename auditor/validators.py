from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

SMART_SMALL_WORDS = {"a", "o", "de", "da", "do", "e", "em", "para", "com", "por", "das", "dos", "na", "no", "um", "uma"}

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# word -> what to say when it shows up (PT-BR usage in PT-PT copy). Non-blocking lint only.
BRAZILIAN_LINT_TERMS: dict[str, str] = {
    "você": 'usa "você" (PT-BR); em PT-PT trata-se por "o/a utilizador(a)" ou omite-se o sujeito',
    "vocês": 'usa "vocês" (PT-BR); em PT-PT prefira "os/as utilizadores(as)" ou omita o sujeito',
    "ônibus": 'em PT-PT diz-se "autocarro"',
    "celular": 'em PT-PT diz-se "telemóvel"',
    "tela": 'em PT-PT diz-se "ecrã"',
    "arquivo": 'em PT-PT diz-se "ficheiro"',
    "usuário": 'em PT-PT diz-se "utilizador"',
    "usuária": 'em PT-PT diz-se "utilizadora"',
    "time": 'no sentido de equipa, em PT-PT diz-se "equipa"',
    "grátis": None,  # kept out of the lint list on purpose: valid in both variants
}
_BRAZILIAN_LINT_TERMS = {k: v for k, v in BRAZILIAN_LINT_TERMS.items() if v}

# Superlativos e promessas sem base que um anúncio só pode usar se constarem literalmente
# nalguma citação das ofertas verificadas do site (secção E.2 do pedido de correcção).
UNSUPPORTED_SUPERLATIVES = [
    "líder", "lider", "melhor", "número 1", "numero 1", "nº 1", "n.º 1", "n.º1",
    "garantido", "garantida", "garantidos", "garantidas", "em tempo real",
]


class AssetCheck(BaseModel):
    text: str
    chars: int
    valid: bool
    warnings: bool = False
    unverified: bool = False
    issues: list[str] = Field(default_factory=list)
    lint: list[str] = Field(default_factory=list)


class SitelinkCheck(BaseModel):
    text: AssetCheck
    descriptions: list[AssetCheck]
    url: str = ""
    url_valid: bool = True


def load_validation_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["validation"]


def lint_pt_pt(text: str) -> list[str]:
    """Non-blocking PT-BR/pronoun lint. Never affects `valid`. "seu"/"sua" nunca são marcados
    (secção E.4): são de uso corrente e correcto em PT-PT (ex.: "o seu carro"), e marcá-los
    dava demasiados falsos positivos - só "você"/"vocês" e brasileirismos claros de vocabulário
    contam. Cada aviso mostra a palavra exacta encontrada."""
    lower = text.lower()
    issues = []
    for term, message in _BRAZILIAN_LINT_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", lower):
            issues.append(f'"{term}": {message}')
    return issues


def check_unsupported_claims(text: str, verified_quotes: list[str]) -> list[str]:
    """Superlativos/promessas sem base (secção E.2): só passam se a mesma palavra constar
    literalmente numa citação das ofertas verificadas do site. Devolve as que faltam."""
    lower = text.lower()
    combined_quotes = " ".join(q.lower() for q in verified_quotes)
    return [term for term in UNSUPPORTED_SUPERLATIVES if term in lower and term not in combined_quotes]


def is_title_case(text: str, mode: str = "all_words") -> bool:
    words = text.split()
    for index, raw_word in enumerate(words):
        match = WORD_RE.search(raw_word)
        if not match:
            continue  # no letters in this token (e.g. "100%", "€", "24/7") - nothing to check
        word = match.group(0)
        if mode == "smart" and index > 0 and word.lower() in SMART_SMALL_WORDS:
            if word != word.lower():
                return False
            continue
        if not word[0].isupper():
            return False
    return True


def check_forbidden_words(text: str, forbidden_words: list[str]) -> list[str]:
    lower = text.lower()
    return [w for w in forbidden_words if re.search(rf"\b{re.escape(w.lower())}\b", lower)]


def validate_asset(
    text: str,
    *,
    min_len: Optional[int] = None,
    max_len: Optional[int] = None,
    title_case: bool = False,
    title_case_mode: str = "all_words",
    forbidden_words: Optional[list[str]] = None,
    ends_with_period: bool = False,
    verified_quotes: Optional[list[str]] = None,
) -> AssetCheck:
    chars = len(text)
    issues: list[str] = []

    if min_len is not None and chars < min_len:
        issues.append(f"tem {chars} caracteres, abaixo do mínimo de {min_len}")
    if max_len is not None and chars > max_len:
        issues.append(f"tem {chars} caracteres, acima do máximo de {max_len}")
    if title_case and not is_title_case(text, mode=title_case_mode):
        issues.append("não está em Title Case")
    for word in check_forbidden_words(text, forbidden_words or []):
        issues.append(f'contém a palavra proibida "{word}"')
    if ends_with_period and not text.endswith("."):
        issues.append("não termina em ponto final")

    unverified = False
    if verified_quotes is not None:
        unsupported = check_unsupported_claims(text, verified_quotes)
        if unsupported:
            unverified = True
            issues.append(f"não verificado no site: {', '.join(unsupported)}")

    return AssetCheck(text=text, chars=chars, valid=not issues, unverified=unverified, issues=issues, lint=lint_pt_pt(text))


def validate_group(
    texts: list[str],
    *,
    min_len: Optional[int] = None,
    max_len: Optional[int] = None,
    title_case: bool = False,
    title_case_mode: str = "all_words",
    forbidden_words: Optional[list[str]] = None,
    ends_with_period: bool = False,
    unique: bool = False,
    verified_quotes: Optional[list[str]] = None,
) -> list[AssetCheck]:
    results = [
        validate_asset(
            t,
            min_len=min_len,
            max_len=max_len,
            title_case=title_case,
            title_case_mode=title_case_mode,
            forbidden_words=forbidden_words,
            ends_with_period=ends_with_period,
            verified_quotes=verified_quotes,
        )
        for t in texts
    ]

    if unique:
        def _dedupe_key(text: str) -> str:
            return re.sub(r"\s+", " ", text.strip().lower())

        seen: dict[str, int] = {}
        for result in results:
            key = _dedupe_key(result.text)
            seen[key] = seen.get(key, 0) + 1
        for result in results:
            key = _dedupe_key(result.text)
            if seen[key] > 1:
                result.issues.append("duplicado: repete outro item deste grupo")
                result.valid = False

    return results


def validate_sitelinks(
    sitelinks: list[dict[str, Any]],
    *,
    text_max: int,
    desc_max: int,
    desc_ends_with_period: bool = False,
    verified_quotes: Optional[list[str]] = None,
    known_urls: Optional[set[str]] = None,
) -> list[SitelinkCheck]:
    checks = []
    for sl in sitelinks:
        url = sl.get("url", "")
        # Sem known_urls não há como verificar (ex.: chamadas antigas/testes que não passam
        # a lista de páginas) - nesse caso nunca se assinala um URL como inválido (secção E.3).
        url_valid = True if known_urls is None else (bool(url) and url in known_urls)
        text_check = validate_asset(sl["text"], max_len=text_max, verified_quotes=verified_quotes)
        if not url_valid:
            text_check.valid = False
            text_check.issues.append(f"o sitelink aponta para um URL que não existe no site rastreado: '{url or '(vazio)'}'")
        checks.append(SitelinkCheck(
            text=text_check,
            descriptions=[validate_asset(d, max_len=desc_max, ends_with_period=desc_ends_with_period, verified_quotes=verified_quotes) for d in sl["descriptions"]],
            url=url,
            url_valid=url_valid,
        ))
    return checks


def validate_primary_text(texts: list[str], *, preview: int = 125, verified_quotes: Optional[list[str]] = None) -> list[AssetCheck]:
    """Meta primary text has no hard character cap, but anything past `preview` characters
    gets truncated in the feed, so flag it as a (non-blocking) warning rather than invalid."""
    results = []
    for text in texts:
        result = validate_asset(text, verified_quotes=verified_quotes)
        if len(text) > preview:
            result.warnings = True
            result.issues.append(f"tem mais de {preview} caracteres: garanta que o gancho está antes desse ponto, porque o resto é cortado na pré-visualização")
        results.append(result)
    return results


def validate_template(key: str, assets: dict[str, Any], config: Optional[dict] = None) -> dict[str, Any]:
    """Validate one template's generated assets (headlines, descriptions, sitelinks...)
    against config.yaml's `validation.<key>` rules. Returns the same shape ads.py/the
    frontend expect: each asset replaced by its AssetCheck, `valid` flags included.
    """
    config = config or load_validation_config()
    rules = config[key]
    title_case_mode = config.get("title_case_mode", "all_words")

    report: dict[str, Any] = {}

    if "headlines" in rules and "headlines" in assets:
        r = rules["headlines"]
        report["headlines"] = validate_group(
            assets["headlines"],
            max_len=r.get("max"),
            min_len=r.get("min"),
            title_case=r.get("title_case", False),
            title_case_mode=title_case_mode,
            forbidden_words=r.get("forbidden_words"),
            unique=r.get("unique", False),
        )

    if "long_headlines" in rules and "long_headlines" in assets:
        r = rules["long_headlines"]
        report["long_headlines"] = validate_group(assets["long_headlines"], min_len=r.get("min"), max_len=r.get("max"))

    if "descriptions" in rules and "descriptions" in assets:
        r = rules["descriptions"]
        report["descriptions"] = validate_group(
            assets["descriptions"],
            min_len=r.get("min"),
            max_len=r.get("max"),
            ends_with_period=False,
        )

    if "sitelinks" in rules and "sitelinks" in assets:
        r = rules["sitelinks"]
        report["sitelinks"] = validate_sitelinks(
            assets["sitelinks"],
            text_max=r["text_max"],
            desc_max=r["desc_max"],
            desc_ends_with_period=r.get("desc_ends_with_period", False),
        )

    if "primary_text" in rules and "primary_text" in assets:
        r = rules["primary_text"]
        report["primary_text"] = validate_primary_text(assets["primary_text"], preview=r.get("preview", 125))

    return report


def count_valid(results: list[AssetCheck]) -> tuple[int, int]:
    return sum(1 for r in results if r.valid), len(results)


def failed_items(results: list[AssetCheck]) -> list[AssetCheck]:
    """The subset that still needs a correction round, with the exact character counts an
    LLM correction prompt should be given (per secção 3, ponto 4 do briefing)."""
    return [r for r in results if not r.valid]
