import json

import pytest

from auditor.analysis import (
    CATEGORY_KEYS,
    analyze_communication,
    analyze_page,
    compute_communication_score,
    synthesize_site,
)
from auditor.crawler import PageData
from auditor.llm.base import LLMClient, LLMResponse
from auditor.llm.mock import MockLLMClient

FIXTURES = {"analise": "analysis_page.json", "sintese": "site_synthesis.json"}


class _ScriptedLLMClient(LLMClient):
    """Queues one canned JSON response per call to the "sintese" task, in order - lets a test
    control exactly what synthesize_site's initial call and each top-up call see."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.call_count = 0

    async def generate(self, *, system, prompt, model="", json_schema=None, temperature=0.2):
        self.call_count += 1
        content = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(content=content, model="scripted", backend="scripted")

    async def list_models(self):
        return ["scripted"]

    async def health_check(self):
        return True


def _page(page_id="home", page_type="home") -> PageData:
    return PageData(
        id=page_id,
        url=f"https://example.pt/{page_id}",
        type=page_type,
        title="Página de Teste",
        h1=["Título"],
        main_text="Conteúdo da página de teste.",
    )


@pytest.mark.asyncio
async def test_analyze_page_returns_all_seven_categories():
    llm = MockLLMClient(fixtures=FIXTURES)
    result = await analyze_page(llm, _page())
    assert set(result.categories.keys()) == set(CATEGORY_KEYS)


@pytest.mark.asyncio
async def test_analyze_page_overrides_page_id_with_the_real_one_even_if_llm_gets_it_wrong():
    llm = MockLLMClient(fixtures=FIXTURES)
    result = await analyze_page(llm, _page(page_id="servico-implantologia"))
    assert result.page_id == "servico-implantologia"


def test_compute_communication_score_averages_forte_fraco_ausente_and_skips_nao_aplicavel():
    from auditor.analysis import CategoryAnalysis, CTAAnalysis, PageCommunication

    page = PageCommunication(
        page_id="x",
        categories={
            "problemas": CategoryAnalysis(level="forte", evidence="e", recommendation="r"),
            "caracteristicas": CategoryAnalysis(level="fraco", evidence="e", recommendation="r"),
            "motivos_para_comprar": CategoryAnalysis(level="ausente", evidence="e", recommendation="r"),
            "objeccoes_e_receios": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
            "desejos": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
            "crencas_e_mentalidade": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
            "oportunidades": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
        },
        cta_analysis=CTAAnalysis(clarity="forte", position="topo", note="n"),
    )
    # applicable categories: forte(1) + fraco(0.5) + ausente(0) = 1.5 / 3 = 0.5 -> 50
    assert compute_communication_score([page]) == 50


def test_compute_communication_score_is_zero_with_no_pages():
    assert compute_communication_score([]) == 0


def test_compute_communication_score_only_averages_conversion_pages_when_given():
    from auditor.analysis import CategoryAnalysis, CTAAnalysis, PageCommunication

    def _one_category_page(page_id, level):
        return PageCommunication(
            page_id=page_id,
            categories={
                "problemas": CategoryAnalysis(level=level, evidence="e", recommendation="r"),
                "caracteristicas": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
                "motivos_para_comprar": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
                "objeccoes_e_receios": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
                "desejos": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
                "crencas_e_mentalidade": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
                "oportunidades": CategoryAnalysis(level="nao_aplicavel", evidence="e", recommendation="r"),
            },
            cta_analysis=CTAAnalysis(clarity="forte", position="topo", note="n"),
        )

    home = _one_category_page("home", "forte")  # 100
    faq = _one_category_page("faq", "ausente")  # 0

    # sem conversion_page_ids: a média inclui as duas páginas (secção D.3, comportamento antigo)
    assert compute_communication_score([home, faq]) == 50
    # com conversion_page_ids={"home"}: a FAQ fraca não arrasta a pontuação para baixo
    assert compute_communication_score([home, faq], conversion_page_ids={"home"}) == 100


def test_compute_communication_score_falls_back_to_all_pages_when_conversion_ids_match_none():
    from auditor.analysis import CategoryAnalysis, CTAAnalysis, PageCommunication

    page = PageCommunication(
        page_id="faq",
        categories={key: CategoryAnalysis(level="ausente", evidence="e", recommendation="r") for key in CATEGORY_KEYS},
        cta_analysis=CTAAnalysis(clarity="fraco", position="rodapé", note="n"),
    )
    # conversion_page_ids não vazio mas sem correspondência nenhuma -> nunca fica sem pontuação
    assert compute_communication_score([page], conversion_page_ids={"home"}) == 0


@pytest.mark.asyncio
async def test_synthesize_site_drops_insights_citing_an_unknown_page_id():
    llm = MockLLMClient(fixtures=FIXTURES)
    pages = [_page("home", "home")]
    from auditor.analysis import CategoryAnalysis, CTAAnalysis, PageCommunication

    comm = PageCommunication(
        page_id="home",
        categories={k: CategoryAnalysis(level="forte", evidence="e", recommendation="r") for k in CATEGORY_KEYS},
        cta_analysis=CTAAnalysis(clarity="forte", position="topo", note="n"),
    )
    synthesis = await synthesize_site(llm, pages, [comm])
    # fixture cites "servico" and "precos" which don't exist among `pages` (only "home" does)
    for items in synthesis.insights.values():
        assert all(item.source_page == "home" for item in items)
    assert all(t.page == "home" for t in synthesis.top_improvements)


@pytest.mark.asyncio
async def test_analyze_communication_runs_full_step_and_returns_score():
    llm = MockLLMClient(fixtures=FIXTURES)
    pages = [_page("home", "home"), _page("servico", "servico")]
    communications, synthesis, score = await analyze_communication(llm, pages)
    assert len(communications) == 2
    assert isinstance(score, int)
    assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# O top 10 de melhorias devolve sempre 10 (secção F.1 do pedido de correcção)
# ---------------------------------------------------------------------------


def _improvement(title: str) -> dict:
    return {"title": title, "impact": "alto", "effort": "baixo", "page": "home"}


@pytest.mark.asyncio
async def test_synthesize_site_tops_up_top_improvements_to_exactly_ten():
    initial = json.dumps({"insights": {}, "top_improvements": [_improvement(t) for t in "ABCD"]})
    # inclui um duplicado ("A") de propósito - tem de ser filtrado, nunca contado a dobrar.
    followup_1 = json.dumps({"items": [_improvement(t) for t in "AEFG"]})
    followup_2 = json.dumps({"items": [_improvement(t) for t in "HIJ"]})

    llm = _ScriptedLLMClient([initial, followup_1, followup_2])
    pages = [_page("home", "home")]
    from auditor.analysis import CategoryAnalysis, CTAAnalysis, PageCommunication

    comm = PageCommunication(
        page_id="home",
        categories={k: CategoryAnalysis(level="forte", evidence="e", recommendation="r") for k in CATEGORY_KEYS},
        cta_analysis=CTAAnalysis(clarity="forte", position="topo", note="n"),
    )

    synthesis = await synthesize_site(llm, pages, [comm])

    assert len(synthesis.top_improvements) == 10
    titles = [t.title for t in synthesis.top_improvements]
    assert len(set(titles)) == 10  # nenhum duplicado
    assert llm.call_count == 3  # 1 chamada inicial + 2 de reforço


@pytest.mark.asyncio
async def test_synthesize_site_stops_topping_up_once_the_model_stops_offering_anything_new():
    initial = json.dumps({"insights": {}, "top_improvements": [_improvement(t) for t in "ABCD"]})
    only_duplicates = json.dumps({"items": [_improvement(t) for t in "ABCD"]})  # nada de novo

    llm = _ScriptedLLMClient([initial, only_duplicates, only_duplicates, only_duplicates])
    pages = [_page("home", "home")]
    from auditor.analysis import CategoryAnalysis, CTAAnalysis, PageCommunication

    comm = PageCommunication(
        page_id="home",
        categories={k: CategoryAnalysis(level="forte", evidence="e", recommendation="r") for k in CATEGORY_KEYS},
        cta_analysis=CTAAnalysis(clarity="forte", position="topo", note="n"),
    )

    synthesis = await synthesize_site(llm, pages, [comm])

    # nunca inventa nada e nunca fica pendurado a tentar para sempre - pára logo na primeira
    # tentativa de reforço sem nada de novo, em vez de gastar as MAX_TOP_IMPROVEMENTS_ATTEMPTS.
    assert len(synthesis.top_improvements) == 4
    assert llm.call_count == 2
