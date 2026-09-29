"""Testes para o bloco A do pedido de correcção: sementes de um setor errado (ex.: "agência
de marketing digital" para um site de transportes e logística).

Causa identificada: as sementes vinham de uma chamada de IA separada (infer_seed_terms, já
removida) cujo prompt só recebia `_pages_summary` (tipo + título de cada página) - um resumo
muito mais pobre do que o que o Perfil já usa (texto completo, H1, meta description), e sem
nenhuma validação de que a semente devolvida tivesse alguma relação com o site. Um modelo
pequeno/gratuito, com tão pouco contexto, tende a "completar" com o arquétipo de negócio mais
comum nos dados de treino (uma agência de marketing digital) em vez de se ancorar no site real.
A correcção: as sementes passam a derivar do Perfil já extraído (sector, produto ou serviço) -
que corre antes dos Termos no pipeline - com fallback para títulos/H1 de páginas de
serviço/produto, e cada candidato é validado contra o vocabulário do próprio site/Perfil."""

import httpx
import pytest

from auditor.crawler import PageData
from auditor.keywords import (
    _clean_negative_keywords,
    build_keywords,
    derive_seed_terms,
    derive_seed_terms_from_profile,
    filter_observed_terms,
)
from auditor.llm.mock import MockLLMClient

FIXTURES = {"keywords": "keywords_synthesis.json"}


def _page(page_id="home", page_type="home", title=None, h1=None, meta_description="") -> PageData:
    return PageData(id=page_id, url=f"https://example.pt/{page_id}", type=page_type, title=title or "", h1=h1 or [], meta_description=meta_description)


def _profile_field(value: str) -> dict:
    return {"value": value, "origin": "site", "confidence": 0.9}


TRANSPORT_PROFILE = {
    "sector": _profile_field("Transportes e logística"),
    "produto_ou_servico": _profile_field("Transporte de mercadorias, mudanças e distribuição"),
    "empresa": _profile_field("David Neto Transportes"),
    "geografia": _profile_field("Portugal"),
}

TRANSPORT_PAGES = [
    _page("home", "home", title="David Neto | Transportes e Mudanças"),
    _page("servicos", "servico", title="David Neto | Serviços", h1=["Transporte de mercadorias"]),
]


def test_derive_seed_terms_from_profile_uses_sector_and_product_only():
    seeds = derive_seed_terms_from_profile(TRANSPORT_PROFILE)
    assert any("transporte" in s for s in seeds)
    # nunca deve inventar nada fora do que está no sector/produto_ou_servico
    assert all("marketing" not in s and "seo" not in s for s in seeds)


def test_derive_seed_terms_grounds_seeds_in_the_profile_not_in_an_unrelated_sector(capsys):
    """Reproduz o sintoma relatado: mesmo que uma fonte devolva uma semente de um setor
    completamente diferente do site, ela nunca deve sobreviver à validação de relevância."""
    off_topic_profile = {
        "sector": _profile_field("Agência de marketing digital"),  # setor errado, propositadamente
        "produto_ou_servico": _profile_field("Transporte de mercadorias"),  # este está certo
        "empresa": _profile_field("David Neto Transportes"),
    }
    seeds = derive_seed_terms(TRANSPORT_PAGES, profile=off_topic_profile)

    assert not any("marketing" in s or "agência" in s for s in seeds)
    assert any("transporte" in s for s in seeds)
    discarded_log = capsys.readouterr().out
    assert "descartada" in discarded_log
    assert "marketing" in discarded_log or "agência" in discarded_log


def test_derive_seed_terms_never_uses_owner_services_as_a_seed_source():
    """owner_services (Google Ads, Meta Ads...) é sobre o que o Auditor vende - nunca deve
    ser usado como fonte de sementes para o negócio auditado (secção A.2)."""
    profile_without_owner_services_leak = TRANSPORT_PROFILE
    seeds = derive_seed_terms(TRANSPORT_PAGES, profile=profile_without_owner_services_leak)
    forbidden = {"google ads", "meta ads", "microsoft advertising", "tracking e analytics", "copy e conversão"}
    assert not (set(seeds) & forbidden)


def test_derive_seed_terms_falls_back_to_service_titles_when_profile_has_fewer_than_three():
    thin_profile = {"sector": _profile_field("Transportes")}  # só 1 fragmento válido possível
    pages = [
        _page("home", "home", title="David Neto | Transportes e Mudanças", h1=["Transportes e mudanças rápidas"]),
        _page("servicos", "servico", title="Mudanças de casa", h1=["Mudanças de casa"]),
        _page("produto", "produto", title="Transporte internacional", h1=["Transporte internacional"]),
    ]
    seeds = derive_seed_terms(pages, profile=thin_profile)
    assert len(seeds) >= 3


def test_filter_observed_terms_removes_other_markets_spanish_and_noise_and_caps_and_logs(capsys):
    seeds = ["transporte de mercadorias", "mudanças casa"]
    terms = (
        ["transporte de mercadorias em lisboa", "mudanças casa porto"]
        + [f"transporte mercadorias {i}" for i in range(70)]  # força o corte no limite
        + ["transporte mercadorias madrid", "mudanças casa barcelona", "transporte sao paulo"]
        + ["camiones de transporte", "precio transporte mercadorias"]
        + ["curso transporte mercadorias", "vagas transporte mercadorias", "transporte mercadorias pdf"]
    )
    result = filter_observed_terms(terms, seeds, cap=60)

    assert len(result) <= 60
    assert not any("madrid" in t or "barcelona" in t or "sao paulo" in t for t in result)
    assert not any("camiones" in t or "precio" in t for t in result)
    assert not any("curso" in t or "vagas" in t or "pdf" in t for t in result)

    log = capsys.readouterr().out
    assert "descartados" in log


def test_filter_observed_terms_keeps_only_terms_sharing_a_word_with_a_seed():
    seeds = ["transporte de mercadorias"]
    terms = ["transporte de mercadorias urgente", "agência de viagens económicas"]
    result = filter_observed_terms(terms, seeds)
    assert result == ["transporte de mercadorias urgente"]


def test_clean_negative_keywords_splits_glued_items_into_separate_chips():
    raw = ["vagas,emprego,estágio", "pdf", "reclame aqui / reclameaqui"]
    cleaned = _clean_negative_keywords(raw)
    assert "vagas" in cleaned
    assert "emprego" in cleaned
    assert "estágio" in cleaned
    assert "pdf" in cleaned
    assert all("," not in c for c in cleaned)


@pytest.mark.asyncio
async def test_build_keywords_grounds_seeds_in_the_profile_end_to_end(tmp_path):
    """Ponta-a-ponta: build_keywords recebe o Perfil (como já acontece no pipeline, onde o
    Perfil corre antes dos Termos) e as sementes resultantes ficam mesmo assim no setor certo."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q")
        return httpx.Response(200, json=[query, [f"{query} urgente"]])

    llm = MockLLMClient(fixtures=FIXTURES)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await build_keywords(
            llm, client, TRANSPORT_PAGES, profile=TRANSPORT_PROFILE, cache_dir=tmp_path, use_alphabet=False, delay_seconds=0,
        )

    observed_terms = [o["term"] for o in result["observed"]]
    assert all("marketing" not in t and "seo" not in t for t in observed_terms)
