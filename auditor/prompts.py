from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
DEFAULTS_DIR = PROMPTS_DIR / "defaults"

PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")

# Placeholder -> profile field mapping (secção 6 do briefing).
PROFILE_FIELD_FOR_PLACEHOLDER: dict[str, str] = {
    "SECTOR": "sector",
    "EMPRESA": "empresa",
    "PÚBLICO": "publico",
    "DESC": "descricao_publico",
    "AÇÃO": "acao",
    "GEOGRAFIA": "geografia",
    "URL DA LANDING PAGE": "landing_page_url",
    "PÁGINA DE DESTINO": "pagina_destino",
    "PRODUTO OU SERVIÇO": "produto_ou_servico",
    "PREÇO BASE ou GAMA DE PREÇOS": "preco",
    "IDADE": "idade",
    "PERFIL": "perfil",
    "NÍVEL": "nivel_poder_compra",
    "MOTIVAÇÃO": "motivacao",
    "OBJECÇÃO": "objecao",
    "COMPORTAMENTO ONLINE": "comportamento_online",
    "OBJETIVO DE CONVERSÃO": "objetivo_conversao",
    "FONTE DE DADOS: ex. lista de clientes, visitantes do site": "fonte_dados_clientes",
    "FONTE DE DADOS: ex. lista de subscritores, clientes actuais": "fonte_dados_subscritores",
    "CONTEÚDO A PROMOVER": "conteudo_a_promover",
    "OBJECTIVO PÓS TRÁFEGO": "objectivo_pos_trafego",
    "MOTIVAÇÃO DE CLIQUE": "motivacao_clique",
    "CANAIS": "canais",
}

# Which mapped placeholders each template actually uses (secção 6). Used to validate
# that a filled prompt has no leftover placeholder from this map.
TEMPLATE_PLACEHOLDERS: dict[str, list[str]] = {
    "02.1_search_leads.md": ["SECTOR", "AÇÃO", "GEOGRAFIA", "URL DA LANDING PAGE"],
    "02.2_pmax_leads.md": ["SECTOR", "AÇÃO", "GEOGRAFIA", "URL DA LANDING PAGE", "PÚBLICO"],
    "02.3_search_ecommerce.md": ["SECTOR", "PRODUTO OU SERVIÇO", "GEOGRAFIA", "URL DA LANDING PAGE"],
    "02.4_pmax_ecommerce.md": ["SECTOR", "PRODUTO OU SERVIÇO", "GEOGRAFIA", "URL DA LANDING PAGE", "PÚBLICO"],
    "02.5_meta_leads.md": [
        "SECTOR",
        "EMPRESA",
        "IDADE",
        "GEOGRAFIA",
        "URL DA LANDING PAGE",
        "OBJETIVO DE CONVERSÃO",
        "FONTE DE DADOS: ex. lista de clientes, visitantes do site",
    ],
    "02.6_meta_ecommerce.md": [
        "SECTOR",
        "EMPRESA",
        "PRODUTO OU SERVIÇO",
        "PREÇO BASE ou GAMA DE PREÇOS",
        "PÚBLICO",
        "DESC",
        "IDADE",
        "PERFIL",
        "NÍVEL",
        "MOTIVAÇÃO",
        "OBJECÇÃO",
        "COMPORTAMENTO ONLINE",
        "GEOGRAFIA",
        "URL DA LANDING PAGE",
    ],
    "02.7_meta_trafego.md": [
        "SECTOR",
        "EMPRESA",
        "PÁGINA DE DESTINO",
        "CONTEÚDO A PROMOVER",
        "OBJECTIVO PÓS TRÁFEGO",
        "PÚBLICO",
        "DESC",
        "IDADE",
        "PERFIL",
        "COMPORTAMENTO ONLINE",
        "MOTIVAÇÃO DE CLIQUE",
        "CANAIS",
        "GEOGRAFIA",
        "FONTE DE DADOS: ex. lista de subscritores, clientes actuais",
    ],
}

# Business-model based auto-selection (secção 4.5).
TEMPLATES_FOR_MODEL: dict[str, list[str]] = {
    "leads": ["02.1_search_leads.md", "02.2_pmax_leads.md", "02.5_meta_leads.md"],
    "ecommerce": ["02.3_search_ecommerce.md", "02.4_pmax_ecommerce.md", "02.6_meta_ecommerce.md"],
}
CONTENT_TEMPLATE = "02.7_meta_trafego.md"


def extract_placeholders(text: str) -> set[str]:
    """Return the inner text of every `[...]` placeholder found in `text`."""
    return {match[1:-1] for match in PLACEHOLDER_RE.findall(text)}


def load_template(name: str, *, defaults: bool = False) -> str:
    directory = DEFAULTS_DIR if defaults else PROMPTS_DIR
    return (directory / name).read_text(encoding="utf-8")


def list_template_names() -> list[str]:
    return sorted(TEMPLATE_PLACEHOLDERS.keys())


def fill_template(name: str, profile: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute this template's mapped placeholders with profile values.

    Returns the filled text and the list of mapped placeholders that had no value in
    `profile` (left untouched in the text, and worth flagging in the report). Any bracket
    text outside the map (e.g. the literal "[X]" example in 02.7) is never touched.
    """
    text = load_template(name)
    missing: list[str] = []
    for placeholder in TEMPLATE_PLACEHOLDERS.get(name, []):
        field = PROFILE_FIELD_FOR_PLACEHOLDER[placeholder]
        value = profile.get(field)
        if value is None or value == "":
            missing.append(placeholder)
            continue
        text = text.replace(f"[{placeholder}]", value)
    return text, missing
