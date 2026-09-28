import json

import pytest

from auditor.prompts import (
    DEFAULTS_DIR,
    PROFILE_FIELD_FOR_PLACEHOLDER,
    PROMPTS_DIR,
    TEMPLATE_PLACEHOLDERS,
    extract_placeholders,
    fill_template,
    load_template,
)


@pytest.mark.parametrize("name", sorted(TEMPLATE_PLACEHOLDERS.keys()))
def test_template_contains_its_mapped_placeholders(name):
    text = load_template(name)
    found = extract_placeholders(text)
    expected = set(TEMPLATE_PLACEHOLDERS[name])
    missing = expected - found
    assert not missing, f"{name} is missing placeholders: {missing}"


@pytest.mark.parametrize("name", sorted(TEMPLATE_PLACEHOLDERS.keys()))
def test_default_copy_matches_editable_copy(name):
    editable = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    default = (DEFAULTS_DIR / name).read_text(encoding="utf-8")
    assert editable == default


def test_every_mapped_placeholder_has_a_profile_field():
    for name, placeholders in TEMPLATE_PLACEHOLDERS.items():
        for placeholder in placeholders:
            assert placeholder in PROFILE_FIELD_FOR_PLACEHOLDER, f"{placeholder} (used by {name}) has no profile field mapping"


def test_02_7_example_placeholder_x_is_not_in_the_map():
    text = load_template("02.7_meta_trafego.md")
    assert "[X]" in text
    assert "X" not in TEMPLATE_PLACEHOLDERS["02.7_meta_trafego.md"]


def test_fill_template_substitutes_mapped_placeholders_and_flags_missing():
    profile = json.load(open("tests/fixtures/demo_audit.json", encoding="utf-8"))["profile"]
    flat_profile = {k: v["value"] for k, v in profile.items() if k != "business_model"}

    filled, missing = fill_template("02.1_search_leads.md", flat_profile)

    assert missing == []
    assert "[SECTOR]" not in filled
    assert "[URL DA LANDING PAGE]" not in filled
    assert flat_profile["sector"] in filled
    assert flat_profile["landing_page_url"] in filled


def test_fill_template_reports_missing_placeholder_without_touching_it():
    filled, missing = fill_template("02.1_search_leads.md", {})
    assert set(missing) == {"SECTOR", "AÇÃO", "GEOGRAFIA", "URL DA LANDING PAGE"}
    assert "[SECTOR]" in filled


def test_02_7_keeps_the_literal_example_placeholder_untouched():
    filled, _ = fill_template("02.7_meta_trafego.md", {})
    assert "[X]" in filled
