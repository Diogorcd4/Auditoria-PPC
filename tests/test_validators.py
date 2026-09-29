import pytest

from auditor.validators import (
    check_forbidden_words,
    check_unsupported_claims,
    count_valid,
    failed_items,
    is_title_case,
    lint_pt_pt,
    load_validation_config,
    validate_asset,
    validate_group,
    validate_primary_text,
    validate_sitelinks,
    validate_template,
)

# ---------------------------------------------------------------------------
# Character limits: exact boundaries matter (30, 85/90, 25/35, 40)
# ---------------------------------------------------------------------------


def test_max_len_boundary_is_inclusive():
    exactly_30 = "a" * 30
    over_30 = "a" * 31
    assert validate_asset(exactly_30, max_len=30).valid is True
    result = validate_asset(over_30, max_len=30)
    assert result.valid is False
    assert "31 caracteres" in result.issues[0]


def test_min_max_window_85_90_inclusive_on_both_ends():
    assert validate_asset("a" * 85, min_len=85, max_len=90).valid is True
    assert validate_asset("a" * 90, min_len=85, max_len=90).valid is True
    assert validate_asset("a" * 84, min_len=85, max_len=90).valid is False
    assert validate_asset("a" * 91, min_len=85, max_len=90).valid is False


def test_accented_characters_count_as_a_single_character_each():
    # "ção" has 3 accented/composed characters; Python len() on NFC text must match that,
    # exactly like a human counting characters in the Google Ads character-count UI.
    text = "Avaliação Grátis Já"  # 19 characters
    assert len(text) == 19
    assert validate_asset(text, max_len=19).valid is True
    assert validate_asset(text, max_len=18).valid is False


def test_sitelink_text_and_description_limits_25_and_35():
    sitelinks = [{"text": "a" * 25, "descriptions": ["b" * 35, "c" * 35]}]
    checks = validate_sitelinks(sitelinks, text_max=25, desc_max=35)
    assert checks[0].text.valid is True
    assert all(d.valid for d in checks[0].descriptions)

    sitelinks_over = [{"text": "a" * 26, "descriptions": ["b" * 36, "c" * 10]}]
    checks_over = validate_sitelinks(sitelinks_over, text_max=25, desc_max=35)
    assert checks_over[0].text.valid is False
    assert checks_over[0].descriptions[0].valid is False
    assert checks_over[0].descriptions[1].valid is True


def test_meta_headline_and_description_limits_40_and_30():
    assert validate_asset("a" * 40, max_len=40).valid is True
    assert validate_asset("a" * 41, max_len=40).valid is False
    assert validate_asset("a" * 30, max_len=30).valid is True
    assert validate_asset("a" * 31, max_len=30).valid is False


# ---------------------------------------------------------------------------
# Title Case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Avaliação Gratuita Hoje",
        "15 Anos A Tratar Sorrisos",
        "Implantes Sem Dor Em Lisboa",
        "24/7 Suporte Ao Cliente",  # tokens without letters (24/7) are skipped, not failed
    ],
)
def test_is_title_case_all_words_mode_accepts_every_word_capitalized(text):
    assert is_title_case(text, mode="all_words") is True


@pytest.mark.parametrize(
    "text",
    [
        "avaliação Gratuita Hoje",  # first word lowercase
        "Avaliação gratuita Hoje",  # middle word lowercase
        "Avaliação Gratuita hoje",  # last word lowercase
    ],
)
def test_is_title_case_all_words_mode_rejects_any_lowercase_word(text):
    assert is_title_case(text, mode="all_words") is False


def test_is_title_case_smart_mode_lowercases_connectors_except_first_word():
    assert is_title_case("Sorria com Confiança de Novo", mode="smart") is True
    assert is_title_case("A Arte de Sorrir", mode="smart") is True
    # a connector capitalized in smart mode should still pass (spec asks connectors lower,
    # but the important behavioural contrast with all_words is the lowering being *allowed*)
    assert is_title_case("Sorria com Confiança De Novo", mode="all_words") is False


def test_is_title_case_smart_mode_still_requires_first_word_capitalized():
    assert is_title_case("de Novo Sorria", mode="smart") is False


# ---------------------------------------------------------------------------
# Forbidden words (02.1 only): whole word, case-insensitive
# ---------------------------------------------------------------------------


def test_forbidden_words_matches_whole_word_case_insensitive():
    forbidden = ["já", "stress", "sempre"]
    assert check_forbidden_words("Marque Já a Sua Consulta", forbidden) == ["já"]
    assert check_forbidden_words("JÁ HOJE", forbidden) == ["já"]
    assert check_forbidden_words("Sem Stress Nem Pressa", forbidden) == ["stress"]


def test_forbidden_words_does_not_match_as_a_substring():
    # "sempre" must not flag "sempreverde"-style words that merely contain it
    forbidden = ["já", "stress", "sempre"]
    assert check_forbidden_words("Jardim Sempreverde Bonito", forbidden) == []
    assert check_forbidden_words("Ajardinar o Jardim", forbidden) == []


def test_validate_asset_reports_every_forbidden_word_found():
    result = validate_asset("Sempre Já Com Stress", forbidden_words=["já", "stress", "sempre"])
    assert result.valid is False
    assert len(result.issues) == 3


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


def test_validate_group_unique_flags_duplicates_case_and_space_insensitively():
    texts = ["Avaliação Grátis", "avaliação  grátis", "Consulta Rápida"]
    results = validate_group(texts, unique=True)
    assert results[0].valid is False
    assert results[1].valid is False
    assert results[2].valid is True


def test_validate_group_unique_does_not_flag_all_distinct_items():
    texts = ["Um", "Dois", "Três"]
    results = validate_group(texts, unique=True)
    assert all(r.valid for r in results)


# ---------------------------------------------------------------------------
# Sitelink descriptions ending in a period (02.2 / 02.4 only)
# ---------------------------------------------------------------------------


def test_ends_with_period_required_for_02_2_and_02_4_sitelink_descriptions():
    ok = validate_asset("Financiamento em várias prestações.", max_len=35, ends_with_period=True)
    missing = validate_asset("Financiamento em várias prestações", max_len=35, ends_with_period=True)
    assert ok.valid is True
    assert missing.valid is False
    assert "ponto final" in missing.issues[0]


# ---------------------------------------------------------------------------
# Meta primary text: non-blocking preview warning past 125 characters
# ---------------------------------------------------------------------------


def test_primary_text_under_preview_has_no_warning():
    results = validate_primary_text(["Texto curto."], preview=125)
    assert results[0].valid is True
    assert results[0].warnings is False


def test_primary_text_over_preview_is_still_valid_but_warns():
    long_text = "Muito conteúdo aqui. " * 10  # > 125 chars
    results = validate_primary_text([long_text], preview=125)
    assert results[0].valid is True
    assert results[0].warnings is True
    assert "125 caracteres" in results[0].issues[0]


# ---------------------------------------------------------------------------
# PT-PT lint: non-blocking, never affects `valid`
# ---------------------------------------------------------------------------


def test_lint_flags_brazilian_terms_without_failing_validation():
    result = validate_asset("Você vai adorar o seu novo celular", max_len=90)
    assert result.valid is True  # lint never blocks
    assert any("você" in msg for msg in result.lint)
    assert any("celular" in msg for msg in result.lint)


def test_lint_is_empty_for_clean_pt_pt_copy():
    assert lint_pt_pt("Marque a sua consulta de avaliação gratuita") == []


def test_lint_never_flags_seu_or_sua_even_without_an_article():
    """Secção E.4 do pedido de correcção: "seu"/"sua" nunca são marcados pelo lint - só
    "você"/"vocês" e brasileirismos claros de vocabulário."""
    assert lint_pt_pt("Marque seu horário hoje mesmo") == []
    assert lint_pt_pt("Sua consulta está confirmada") == []


def test_lint_shows_the_exact_word_found_for_a_brazilianism():
    result = lint_pt_pt("Aceda pelo seu celular a qualquer hora")
    assert result == ['"celular": em PT-PT diz-se "telemóvel"']


# ---------------------------------------------------------------------------
# Ofertas não verificadas: superlativos/promessas sem base (secção E.2)
# ---------------------------------------------------------------------------


def test_check_unsupported_claims_flags_a_superlative_with_no_grounding():
    assert check_unsupported_claims("Somos líderes de mercado", []) == ["líder"]


def test_check_unsupported_claims_allows_a_superlative_literally_in_a_verified_quote():
    quotes = ["A empresa líder em transportes desde 1990."]
    assert check_unsupported_claims("Somos líderes de mercado", quotes) == []


def test_check_unsupported_claims_is_case_insensitive():
    assert check_unsupported_claims("GARANTIDO em 24 horas", []) == ["garantido"]


def test_validate_asset_marks_an_unsupported_superlative_as_unverified_and_invalid():
    result = validate_asset("O melhor serviço de sempre", max_len=90, verified_quotes=[])
    assert result.unverified is True
    assert result.valid is False
    assert any("não verificado no site" in issue for issue in result.issues)


def test_validate_asset_accepts_a_superlative_grounded_in_a_verified_quote():
    result = validate_asset("O melhor serviço de sempre", max_len=90, verified_quotes=["O melhor serviço da região, garantido."])
    assert result.unverified is False
    assert result.valid is True


def test_validate_asset_skips_the_unsupported_claims_check_when_verified_quotes_is_none():
    """Compatibilidade: chamadas antigas que não passam verified_quotes continuam a validar
    só pelas regras de sempre (comprimento, title case...), sem este novo bloqueio."""
    result = validate_asset("O melhor serviço de sempre", max_len=90)
    assert result.unverified is False
    assert result.valid is True


# ---------------------------------------------------------------------------
# Sitelinks: o URL tem de existir no site rastreado (secção E.3)
# ---------------------------------------------------------------------------


def test_validate_sitelinks_flags_a_url_that_does_not_exist_on_the_site():
    sitelinks = [{"text": "Contacte-nos", "url": "https://example.pt/pagina-inexistente", "descriptions": ["Fale connosco agora mesmo."]}]
    checks = validate_sitelinks(sitelinks, text_max=25, desc_max=35, known_urls={"https://example.pt/", "https://example.pt/contacto"})
    assert checks[0].url_valid is False
    assert checks[0].text.valid is False
    assert any("não existe no site rastreado" in issue for issue in checks[0].text.issues)


def test_validate_sitelinks_accepts_a_url_that_exists_on_the_site():
    sitelinks = [{"text": "Contacte-nos", "url": "https://example.pt/contacto", "descriptions": ["Fale connosco agora mesmo."]}]
    checks = validate_sitelinks(sitelinks, text_max=25, desc_max=35, known_urls={"https://example.pt/", "https://example.pt/contacto"})
    assert checks[0].url_valid is True
    assert checks[0].text.valid is True


def test_validate_sitelinks_never_flags_a_url_when_known_urls_is_not_given():
    sitelinks = [{"text": "Contacte-nos", "url": "https://example.pt/qualquer-coisa", "descriptions": ["Fale connosco agora mesmo."]}]
    checks = validate_sitelinks(sitelinks, text_max=25, desc_max=35)
    assert checks[0].url_valid is True


# ---------------------------------------------------------------------------
# count_valid / failed_items helpers
# ---------------------------------------------------------------------------


def test_count_valid_and_failed_items():
    results = validate_group(["a" * 30, "a" * 31, "a" * 32], max_len=30)
    valid, total = count_valid(results)
    assert (valid, total) == (1, 3)
    assert len(failed_items(results)) == 2


# ---------------------------------------------------------------------------
# Full-template validation against the real config.yaml rules
# ---------------------------------------------------------------------------


def _valid_sitelinks(n, text_max, desc_max, period=False):
    suffix = "." if period else ""
    return [{"text": f"Link {i}"[:text_max], "descriptions": [f"Desc curta{suffix}", f"Outra desc{suffix}"]} for i in range(n)]


def test_validate_template_02_1_with_fully_compliant_assets():
    assets = {
        "headlines": [f"Palavra Numero {i}" for i in range(15)],
        "descriptions": ["a" * 85] * 4,
        "sitelinks": _valid_sitelinks(6, 25, 35),
    }
    report = validate_template("02.1", assets)
    assert all(r.valid for r in report["headlines"])
    assert all(r.valid for r in report["descriptions"])
    assert all(sl.text.valid for sl in report["sitelinks"])


def test_validate_template_02_1_flags_forbidden_word_and_non_title_case():
    assets = {
        "headlines": ["sempre disponível para si"] + [f"Palavra Numero {i}" for i in range(14)],
        "descriptions": ["a" * 85] * 4,
        "sitelinks": _valid_sitelinks(6, 25, 35),
    }
    report = validate_template("02.1", assets)
    assert report["headlines"][0].valid is False
    assert any("proibida" in issue for issue in report["headlines"][0].issues)
    assert any("Title Case" in issue for issue in report["headlines"][0].issues)


def test_validate_template_02_2_requires_sitelink_descriptions_to_end_with_period():
    assets = {
        "headlines": [f"Palavra Numero {i}" for i in range(15)],
        "long_headlines": ["a" * 85] * 5,
        "descriptions": ["a" * 85] * 5,
        "sitelinks": _valid_sitelinks(6, 25, 35, period=False),
    }
    report = validate_template("02.2", assets)
    assert all(not d.valid for sl in report["sitelinks"] for d in sl.descriptions)

    assets["sitelinks"] = _valid_sitelinks(6, 25, 35, period=True)
    report_ok = validate_template("02.2", assets)
    assert all(d.valid for sl in report_ok["sitelinks"] for d in sl.descriptions)


def test_validate_template_02_5_meta_leads_uses_40_and_30_limits_and_no_forbidden_words():
    assets = {
        "primary_text": ["Texto principal com gancho." * 3],
        "headlines": ["a" * 40, "a" * 41, "Já Cá Está Tudo Pronto"],
        "descriptions": ["a" * 30, "a" * 31],
    }
    report = validate_template("02.5", assets)
    assert report["headlines"][0].valid is True
    assert report["headlines"][1].valid is False
    # "já" is only forbidden on 02.1 - must not be flagged here
    assert not any("proibida" in issue for issue in report["headlines"][2].issues)
    assert report["descriptions"][0].valid is True
    assert report["descriptions"][1].valid is False


@pytest.mark.parametrize("key", ["02.1", "02.2", "02.3", "02.4", "02.5", "02.6", "02.7"])
def test_load_validation_config_has_every_template(key):
    config = load_validation_config()
    assert key in config
