import json
from pathlib import Path

FIXTURE_PATH = Path("tests/fixtures/demo_audit.json")

CATEGORIES = [
    "problemas",
    "caracteristicas",
    "motivos_para_comprar",
    "objeccoes_e_receios",
    "desejos",
    "crencas_e_mentalidade",
    "oportunidades",
]

VALID_LEVELS = {"forte", "fraco", "ausente", "nao_aplicavel"}


def load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_is_valid_json_with_the_expected_top_level_sections():
    data = load_fixture()
    for key in ("meta", "summary", "pages", "tracking", "communication", "business_categories", "keywords", "opportunities", "profile", "ads", "llm_log"):
        assert key in data, f"missing top-level section: {key}"


def test_meta_counts_match_the_actual_collections():
    data = load_fixture()
    assert data["meta"]["demo"] is True
    assert data["meta"]["pages_analyzed"] == len(data["pages"])
    assert data["meta"]["opportunities_count"] == len(data["opportunities"])
    assert data["meta"]["ads_total_count"] >= data["meta"]["ads_valid_count"] > 0


def test_every_page_has_a_communication_entry_with_all_seven_categories():
    data = load_fixture()
    page_ids = {p["id"] for p in data["pages"]}
    comm_ids = {c["page_id"] for c in data["communication"]["pages"]}
    assert comm_ids == page_ids

    for page in data["communication"]["pages"]:
        assert set(page["categories"].keys()) == set(CATEGORIES)
        for entry in page["categories"].values():
            assert entry["level"] in VALID_LEVELS


def test_business_categories_cover_all_seven_and_cite_a_source_page():
    data = load_fixture()
    assert set(data["business_categories"].keys()) == set(CATEGORIES)
    page_ids = {p["id"] for p in data["pages"]}
    for items in data["business_categories"].values():
        for item in items:
            assert item["source_page"] in page_ids


def test_opportunities_have_priority_service_and_evidence():
    data = load_fixture()
    for opp in data["opportunities"]:
        assert opp["priority"] in {"Alta", "Média", "Baixa"}
        assert opp["service"]
        assert opp["evidence"]


def test_keywords_distinguish_observed_from_inferred():
    data = load_fixture()
    assert data["keywords"]["observed"]
    assert data["keywords"]["inferred"]
    assert data["keywords"]["negatives"]


def test_ads_selected_match_the_leads_business_model():
    data = load_fixture()
    assert data["meta"]["business_model"] == "leads"
    assert set(data["ads"].keys()) == {"02.1", "02.2", "02.5"}
    for block in data["ads"].values():
        assert "headlines" in block
        assert "descriptions" in block


def test_profile_has_no_price_that_is_not_from_the_site():
    data = load_fixture()
    assert data["profile"]["preco"]["value"] == "não indicado no site"
    assert data["profile"]["preco"]["origin"] == "default"
