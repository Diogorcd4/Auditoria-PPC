import json

import pytest

from auditor.ads import (
    TEMPLATE_META,
    build_full_prompt,
    flatten_profile,
    generate_all_ads,
    generate_template_ads,
)
from auditor.crawler import PageData
from auditor.llm.base import LLMClient, LLMResponse
from auditor.llm.mock import TASK_MARKER_RE


class _ScriptedLLMClient(LLMClient):
    """Queues one canned JSON response per call, keyed by the [TASK:...] marker - so a
    multi-call flow (batches, then correction rounds) can be scripted call by call."""

    def __init__(self, responses: dict[str, list[str]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[str] = []

    async def generate(self, *, system, prompt, model="", json_schema=None, temperature=0.2):
        match = TASK_MARKER_RE.search(system)
        task = match.group(1) if match else ""
        self.calls.append(task)
        queue = self.responses.get(task, [])
        content = queue.pop(0) if queue else "{}"
        return LLMResponse(content=content, model="scripted", backend="scripted")

    async def list_models(self):
        return ["scripted"]

    async def health_check(self):
        return True


def _len_text(n: int, prefix: str = "Texto ") -> str:
    if len(prefix) >= n:
        return prefix[:n]
    return prefix + "x" * (n - len(prefix))


VALID_HEADLINES = [
    "Avaliação Gratuita Hoje",
    "Implantes Sem Dor Aqui",
    "Marque A Sua Consulta",
    "Sorria Com Confiança",
    "Equipa Especialista Pronta",
    "Consulta Sem Compromisso",
    "Tratamento Rápido E Seguro",
    "Financiamento Facilitado Aqui",
    "Clínica De Confiança Total",
    "Agende A Sua Visita",
    "Sedação Consciente Disponível",
    "Preços Acessíveis Para Todos",
    "Fale Connosco Hoje Mesmo",
    "Sorria Sem Medo Algum",
    "Cuidados De Qualidade Total",
]


def _profile():
    fields = {
        "sector": "clínicas dentárias",
        "empresa": "Clínica Teste",
        "publico": "Adultos",
        "descricao_publico": "adultos com receio",
        "acao": "marcar consulta",
        "geografia": "Lisboa, Portugal",
        "landing_page_url": "https://example.pt/consulta",
        "pagina_destino": "https://example.pt/consulta",
        "produto_ou_servico": "implantologia",
        "preco": "não indicado no site",
        "idade": "30-55",
        "perfil": "profissionais",
        "nivel_poder_compra": "médio",
        "motivacao": "confiança",
        "objecao": "medo",
        "comportamento_online": "pesquisa no Google",
        "objetivo_conversao": "Pedir Orçamento",
        "fonte_dados_clientes": "lista de clientes",
        "fonte_dados_subscritores": "lista de subscritores",
        "conteudo_a_promover": "artigo",
        "objectivo_pos_trafego": "agendar consulta",
        "motivacao_clique": "curiosidade",
        "canais": "Instagram",
        "business_model": "leads",
    }
    return {k: {"value": v, "origin": "site", "confidence": 0.8} for k, v in fields.items()}


def _pages():
    return [
        PageData(id="home", url="https://example.pt/", type="home", title="Home", main_text="Conteúdo da home." * 20),
        PageData(id="consulta", url="https://example.pt/consulta", type="landing", title="Consulta", main_text="Conteúdo da landing." * 20),
    ]


def test_flatten_profile_extracts_plain_values():
    flat = flatten_profile(_profile())
    assert flat["sector"] == "clínicas dentárias"
    assert flat["empresa"] == "Clínica Teste"


def test_build_full_prompt_fills_placeholders_and_adds_site_content_and_tech_note():
    prompt, missing = build_full_prompt("02.1_search_leads.md", flatten_profile(_profile()), _pages())
    assert missing == []
    assert "[SECTOR]" not in prompt
    assert "clínicas dentárias" in prompt
    assert "CONTEÚDO REAL DO SITE" in prompt
    assert "<<<SITE" in prompt and "SITE>>>" in prompt
    assert "NOTA TÉCNICA" in prompt
    assert "<<<JSON" in prompt and "JSON>>>" in prompt


def test_build_full_prompt_reports_missing_placeholders():
    incomplete = flatten_profile(_profile())
    del incomplete["acao"]
    _, missing = build_full_prompt("02.1_search_leads.md", incomplete, _pages())
    assert missing == ["AÇÃO"]


@pytest.mark.asyncio
async def test_generate_template_ads_02_1_happy_path_no_corrections_needed():
    responses = {
        "anuncios_02.1_headlines": [
            json.dumps({"items": VALID_HEADLINES[0:5]}),
            json.dumps({"items": VALID_HEADLINES[5:10]}),
            json.dumps({"items": VALID_HEADLINES[10:15]}),
        ],
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({
                "sitelinks": [
                    {"text": _len_text(20, f"Link{i} "), "url": "https://example.pt/consulta", "descriptions": [_len_text(30, "Desc "), _len_text(28, "Outra ")]}
                    for i in range(6)
                ]
            })
        ],
    }
    llm = _ScriptedLLMClient(responses)
    block = await generate_template_ads(llm, "02.1_search_leads.md", _profile(), _pages())

    assert block["template"] == "02.1_search_leads.md"
    assert block["platform"] == TEMPLATE_META["02.1_search_leads.md"]["platform"]
    assert len(block["headlines"]) == 15
    assert all(h["valid"] for h in block["headlines"])
    assert len(block["descriptions"]) == 4
    assert all(d["valid"] for d in block["descriptions"])
    assert len(block["sitelinks"]) == 6
    assert all(sl["text"]["valid"] and all(d["valid"] for d in sl["descriptions"]) for sl in block["sitelinks"])
    # headlines batched into exactly 3 calls of 5, per secção 3 do briefing
    assert llm.calls.count("anuncios_02.1_headlines") == 3


@pytest.mark.asyncio
async def test_generate_template_ads_runs_a_correction_round_for_an_invalid_headline():
    headlines_batch3 = VALID_HEADLINES[10:14] + ["Headline Absurdamente Longo De Verdade Aqui"]  # last one > 30 chars
    responses = {
        "anuncios_02.1_headlines": [
            json.dumps({"items": VALID_HEADLINES[0:5]}),
            json.dumps({"items": VALID_HEADLINES[5:10]}),
            json.dumps({"items": headlines_batch3}),
        ],
        "anuncios_02.1_headlines_fix": [json.dumps({"text": "Headline Corrigido Curto"})],
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({
                "sitelinks": [
                    {"text": _len_text(20, f"Link{i} "), "descriptions": [_len_text(30, "Desc "), _len_text(28, "Outra ")]}
                    for i in range(6)
                ]
            })
        ],
    }
    llm = _ScriptedLLMClient(responses)
    block = await generate_template_ads(llm, "02.1_search_leads.md", _profile(), _pages())

    assert len(block["headlines"]) == 15
    assert all(h["valid"] for h in block["headlines"])
    assert block["headlines"][14]["text"] == "Headline Corrigido Curto"
    assert llm.calls.count("anuncios_02.1_headlines_fix") == 1


@pytest.mark.asyncio
async def test_generate_template_ads_gives_up_after_max_correction_rounds_but_still_returns():
    responses = {
        "anuncios_02.1_headlines": [json.dumps({"items": VALID_HEADLINES[0:5]})] * 1,
        # a broken correction loop: always returns the same too-long text
        "anuncios_02.1_headlines_fix": [json.dumps({"text": "Este Texto Continua Demasiado Comprido Sempre"})] * 10,
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({
                "sitelinks": [
                    {"text": _len_text(20, f"Link{i} "), "descriptions": [_len_text(30, "Desc "), _len_text(28, "Outra ")]}
                    for i in range(6)
                ]
            })
        ],
    }
    headlines_with_one_bad = VALID_HEADLINES[0:4] + ["Este Headline Também É Demasiado Comprido Para O Limite De Trinta"]
    responses["anuncios_02.1_headlines"] = [json.dumps({"items": headlines_with_one_bad})]
    llm = _ScriptedLLMClient(responses)

    block = await generate_template_ads(
        llm, "02.1_search_leads.md", _profile(), _pages(),
        max_correction_rounds=3,
    )

    assert llm.calls.count("anuncios_02.1_headlines_fix") == 3  # stops at max_rounds, doesn't loop forever
    assert block["headlines"][4]["valid"] is False  # still invalid after giving up


@pytest.mark.asyncio
async def test_generate_template_ads_flags_missing_placeholders_and_writes_prompt_to_disk(tmp_path):
    incomplete_profile = _profile()
    del incomplete_profile["acao"]
    responses = {
        "anuncios_02.1_headlines": [
            json.dumps({"items": VALID_HEADLINES[0:5]}),
            json.dumps({"items": VALID_HEADLINES[5:10]}),
            json.dumps({"items": VALID_HEADLINES[10:15]}),
        ],
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({
                "sitelinks": [
                    {"text": _len_text(20, f"Link{i} "), "descriptions": [_len_text(30, "Desc "), _len_text(28, "Outra ")]}
                    for i in range(6)
                ]
            })
        ],
    }
    llm = _ScriptedLLMClient(responses)
    block = await generate_template_ads(
        llm, "02.1_search_leads.md", incomplete_profile, _pages(),
        prompts_output_dir=tmp_path,
    )

    assert block["missing_placeholders"] == ["AÇÃO"]
    saved = (tmp_path / "02.1_search_leads.md").read_text(encoding="utf-8")
    assert "[AÇÃO]" in saved


@pytest.mark.asyncio
async def test_generate_all_ads_runs_every_selected_template():
    responses = {
        "anuncios_02.1_headlines": [json.dumps({"items": VALID_HEADLINES[i : i + 5]}) for i in (0, 5, 10)],
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({"sitelinks": [{"text": _len_text(20, f"Link{i} "), "descriptions": [_len_text(30), _len_text(28)]} for i in range(6)]})
        ],
        "anuncios_02.5_primary_text": [json.dumps({"items": ["Texto principal um.", "Texto principal dois.", "Texto principal três."]})],
        "anuncios_02.5_headlines": [json.dumps({"items": ["Implantes Sem Dor", "Avaliação Gratuita", "Sorria De Novo"]})],
        "anuncios_02.5_descriptions": [json.dumps({"items": [_len_text(28, "Curta "), _len_text(29, "Curta "), _len_text(30, "Curta ")]})],
    }
    llm = _ScriptedLLMClient(responses)
    ads = await generate_all_ads(llm, ["02.1_search_leads.md", "02.5_meta_leads.md"], _profile(), _pages())

    assert set(ads.keys()) == {"02.1", "02.5"}
    assert "sitelinks" in ads["02.1"]
    assert "sitelinks" not in ads["02.5"]
    assert len(ads["02.5"]["primary_text"]) == 3
    assert len(ads["02.5"]["headlines"]) == 3


# ---------------------------------------------------------------------------
# Ofertas verificadas: anúncios só podem afirmar o que o site sustenta (secção E)
# ---------------------------------------------------------------------------


VERIFIED_OFFERS = [
    {"claim": "Avaliação gratuita", "url": "https://example.pt/", "quote": "Marque já a sua avaliação gratuita."},
]


def test_build_full_prompt_includes_verified_offers_and_known_urls():
    prompt, _ = build_full_prompt("02.1_search_leads.md", flatten_profile(_profile()), _pages(), verified_offers=VERIFIED_OFFERS)
    assert "OFERTAS VERIFICADAS" in prompt
    assert "Avaliação gratuita" in prompt
    assert "https://example.pt/" in prompt
    assert "líder" in prompt.lower()  # a regra que proíbe superlativos sem base está no prompt


def test_build_full_prompt_says_no_verified_offers_were_found_when_the_list_is_empty():
    prompt, _ = build_full_prompt("02.1_search_leads.md", flatten_profile(_profile()), _pages(), verified_offers=[])
    assert "nenhuma oferta verificada" in prompt.lower()


@pytest.mark.asyncio
async def test_generate_template_ads_marks_an_unsupported_superlative_headline_as_invalid():
    headlines_with_superlative = VALID_HEADLINES[0:4] + ["Somos A Clínica Líder Aqui"]
    responses = {
        "anuncios_02.1_headlines": [json.dumps({"items": headlines_with_superlative})],
        "anuncios_02.1_headlines_fix": [json.dumps({"text": "Somos A Clínica Líder Aqui"})] * 3,  # nunca se corrige
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({"sitelinks": [{"text": _len_text(20, f"Link{i} "), "url": "https://example.pt/consulta", "descriptions": [_len_text(30), _len_text(28)]} for i in range(6)]})
        ],
    }
    llm = _ScriptedLLMClient(responses)
    block = await generate_template_ads(llm, "02.1_search_leads.md", _profile(), _pages(), max_correction_rounds=1, verified_offers=[])

    unsupported = next(h for h in block["headlines"] if "Líder" in h["text"])
    assert unsupported["unverified"] is True
    assert unsupported["valid"] is False
    assert any("não verificado no site" in issue for issue in unsupported["issues"])


@pytest.mark.asyncio
async def test_generate_template_ads_allows_a_superlative_grounded_in_a_verified_offer():
    headlines_with_superlative = VALID_HEADLINES[0:4] + ["Somos A Clínica Líder Aqui"]
    responses = {
        "anuncios_02.1_headlines": [json.dumps({"items": headlines_with_superlative})],
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({"sitelinks": [{"text": _len_text(20, f"Link{i} "), "url": "https://example.pt/consulta", "descriptions": [_len_text(30), _len_text(28)]} for i in range(6)]})
        ],
    }
    grounded_offer = [{"claim": "Líder de mercado", "url": "https://example.pt/", "quote": "Somos a clínica líder na região há 20 anos."}]
    llm = _ScriptedLLMClient(responses)
    block = await generate_template_ads(llm, "02.1_search_leads.md", _profile(), _pages(), verified_offers=grounded_offer)

    grounded = next(h for h in block["headlines"] if "Líder" in h["text"])
    assert grounded["unverified"] is False


@pytest.mark.asyncio
async def test_generate_template_ads_flags_a_sitelink_pointing_to_a_url_outside_the_crawled_site():
    responses = {
        "anuncios_02.1_headlines": [json.dumps({"items": VALID_HEADLINES[0:15]})],
        "anuncios_02.1_descriptions": [json.dumps({"items": [_len_text(87) for _ in range(4)]})],
        "anuncios_02.1_sitelinks": [
            json.dumps({
                "sitelinks": [
                    {"text": _len_text(20, f"Link{i} "), "url": "https://example.pt/nao-existe", "descriptions": [_len_text(30), _len_text(28)]}
                    for i in range(6)
                ]
            })
        ],
        "anuncios_02.1_sitelinks_fix": [json.dumps({"text": "Link", "url": "https://example.pt/nao-existe", "descriptions": ["a", "b"]})] * 10,
    }
    llm = _ScriptedLLMClient(responses)
    block = await generate_template_ads(llm, "02.1_search_leads.md", _profile(), _pages(), max_correction_rounds=1)

    assert all(not sl["url_valid"] for sl in block["sitelinks"])
    assert all(not sl["text"]["valid"] for sl in block["sitelinks"])
