import pytest

from auditor.analysis import (
    CATEGORY_KEYS,
    analyze_communication,
    analyze_page,
    compute_communication_score,
    synthesize_site,
)
from auditor.crawler import PageData
from auditor.llm.mock import MockLLMClient

FIXTURES = {"analise": "analysis_page.json", "sintese": "site_synthesis.json"}


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
