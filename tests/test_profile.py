import pytest

from auditor.crawler import PageData
from auditor.llm.mock import MockLLMClient
from auditor.profile import (
    DEFAULT_GEOGRAFIA,
    PRICE_NOT_ON_SITE,
    ProfileField,
    SiteProfile,
    _apply_safety_rules,
    extract_profile,
    extract_verified_offers,
    pick_best_landing_page,
)

FIXTURES = {"perfil": "site_profile.json"}
OFFERS_FIXTURES = {"perfil": "verified_offers.json"}


def _page(page_id="home", page_type="home", prices=None, url=None) -> PageData:
    return PageData(
        id=page_id,
        url=url or f"https://example.pt/{page_id}",
        type=page_type,
        title="Página",
        prices=prices or [],
        main_text="Conteúdo.",
    )


def _minimal_field(value="x", origin="site", confidence=0.5) -> ProfileField:
    return ProfileField(value=value, origin=origin, confidence=confidence)


def _blank_profile(**overrides) -> SiteProfile:
    fields = {name: _minimal_field() for name in SiteProfile.model_fields if name not in ("conteudo_forte",)}
    fields["business_model"] = _minimal_field(value="leads")
    fields.update(overrides)
    return SiteProfile(conteudo_forte=False, **fields)


@pytest.mark.asyncio
async def test_extract_profile_returns_all_placeholder_fields():
    llm = MockLLMClient(fixtures=FIXTURES)
    pages = [_page("home", "home", prices=["150€"], url="https://example.pt/consulta-gratuita")]
    profile = await extract_profile(llm, pages)
    assert profile.empresa.value == "Clínica Teste"
    assert profile.business_model.value in ("leads", "ecommerce")


def test_pick_best_landing_page_prefers_landing_then_servico_then_home():
    pages = [_page("home", "home"), _page("servico", "servico"), _page("landing", "landing")]
    assert pick_best_landing_page(pages).id == "landing"

    pages_no_landing = [_page("home", "home"), _page("servico", "servico")]
    assert pick_best_landing_page(pages_no_landing).id == "servico"

    assert pick_best_landing_page([]) is None


def test_safety_rule_forces_price_not_on_site_when_no_page_mentions_a_price():
    profile = _blank_profile(preco=_minimal_field(value="150€", origin="site"))
    pages = [_page("home", prices=[])]  # no prices anywhere on the crawled site
    fixed = _apply_safety_rules(profile, pages)
    assert fixed.preco.value == PRICE_NOT_ON_SITE
    assert fixed.preco.origin == "default"


def test_safety_rule_keeps_price_when_a_page_actually_mentions_one():
    profile = _blank_profile(preco=_minimal_field(value="150€", origin="site"))
    pages = [_page("home", prices=["150€"])]
    fixed = _apply_safety_rules(profile, pages)
    assert fixed.preco.value == "150€"


def test_safety_rule_defaults_empty_geografia_to_portugal():
    profile = _blank_profile(geografia=_minimal_field(value="", origin="inferido"))
    fixed = _apply_safety_rules(profile, [_page("home")])
    assert fixed.geografia.value == DEFAULT_GEOGRAFIA
    assert fixed.geografia.origin == "default"


def test_safety_rule_replaces_a_landing_url_that_does_not_exist_on_the_site():
    profile = _blank_profile(landing_page_url=_minimal_field(value="https://invented.example/not-real"))
    pages = [_page("home", "home", url="https://example.pt/"), _page("servico", "servico", url="https://example.pt/servicos")]
    fixed = _apply_safety_rules(profile, pages)
    assert fixed.landing_page_url.value in {p.url for p in pages}
    assert fixed.landing_page_url.value == "https://example.pt/servicos"  # servico outranks home


def test_safety_rule_falls_back_to_leads_for_an_invalid_business_model_value():
    profile = _blank_profile(business_model=_minimal_field(value="alguma-coisa-estranha"))
    fixed = _apply_safety_rules(profile, [_page("home")])
    assert fixed.business_model.value == "leads"
    assert fixed.business_model.origin == "default"


# ---------------------------------------------------------------------------
# Ofertas verificadas (secção E.1 do pedido de correcção)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_verified_offers_returns_claims_grounded_in_real_pages():
    llm = MockLLMClient(fixtures=OFFERS_FIXTURES)
    pages = [_page("home", url="https://example.pt/home")]
    offers = await extract_verified_offers(llm, pages)

    claims = {o.claim for o in offers}
    assert "Consulta de avaliação gratuita" in claims
    assert all(o.url == "https://example.pt/home" for o in offers)


@pytest.mark.asyncio
async def test_extract_verified_offers_drops_any_offer_whose_url_is_not_a_known_page():
    """Nunca confia numa oferta cujo URL o modelo inventou (secção E.1): tem de ser uma das
    páginas realmente rastreadas."""
    llm = MockLLMClient(fixtures=OFFERS_FIXTURES)
    pages = [_page("home", url="https://example.pt/home")]
    offers = await extract_verified_offers(llm, pages)

    assert not any(o.url == "https://example.pt/nao-existe" for o in offers)
    assert not any("inexistente" in o.claim.lower() for o in offers)


@pytest.mark.asyncio
async def test_extract_verified_offers_is_empty_when_the_llm_finds_nothing():
    llm = MockLLMClient(fixtures={"perfil": "default_response.json"})
    pages = [_page("home", url="https://example.pt/home")]
    offers = await extract_verified_offers(llm, pages)
    assert offers == []
