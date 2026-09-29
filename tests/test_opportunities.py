from auditor.opportunities import derive_opportunities

OWNER_SERVICES = ["Google Ads", "Meta Ads", "Microsoft Advertising", "Tracking e Analytics", "Copy e conversão"]


def _entry(detected=False, state="Não detetado", **extra):
    return {"detected": detected, "state": state, "id": None, "evidence": [], **extra}


def _tracking(**overrides):
    base = {
        "ga4": _entry(),
        "ua": _entry(),
        "gtm": _entry(),
        "google_ads": _entry(),
        "meta_pixel": _entry(events_observed=[]),
        "microsoft_uet": _entry(),
        "consent_mode": {"detected": False, "state": "Não detetado"},
        "consent_interaction": {"cmp": "Não detetado", "banner_found": False, "click_attempted": False, "click_had_effect": False},
        "ecommerce_events_in_datalayer": [],
    }
    base.update(overrides)
    return base


def test_no_tracking_at_all_yields_high_priority_opportunities_for_every_platform():
    opportunities = derive_opportunities(_tracking(), [], OWNER_SERVICES)
    services = {o["service"] for o in opportunities}
    assert "Google Ads" in services
    assert "Meta Ads" in services
    assert "Microsoft Advertising" in services
    assert any(o["priority"] == "Alta" for o in opportunities if o["service"] == "Google Ads")


def test_ga4_detected_with_conversion_events_does_not_flag_ga4_opportunities():
    tracking = _tracking(
        ga4=_entry(detected=True, state="A disparar sem consentimento"),
        ecommerce_events_in_datalayer=["generate_lead"],
    )
    opportunities = derive_opportunities(tracking, [], OWNER_SERVICES)
    assert not any("GA4" in o["evidence"] or "Google Analytics 4" in o["evidence"] for o in opportunities)


def test_ga4_without_conversion_events_flags_a_non_verifiable_medium_priority_opportunity():
    tracking = _tracking(ga4=_entry(detected=True, state="A disparar só após consentimento"))
    opportunities = derive_opportunities(tracking, [], OWNER_SERVICES)
    matches = [o for o in opportunities if o["service"] == "Tracking e Analytics" and "eventos de conversão" in o["pitch"]]
    assert matches
    assert matches[0]["priority"] == "Média"
    assert "não clica nem submete formulários" in matches[0]["evidence"]


def test_ga4_present_in_code_but_never_firing_flags_a_silent_tag_opportunity():
    tracking = _tracking(ga4=_entry(detected=True, state="No código, sem disparo observado", id="G-TEST"))
    opportunities = derive_opportunities(tracking, [], OWNER_SERVICES)
    matches = [o for o in opportunities if o["service"] == "Tracking e Analytics" and "G-TEST" in o["evidence"]]
    assert matches
    # nunca a mensagem de "nenhuma tag detetada" para uma tag que está presente no código
    assert not any("Nenhuma tag do Google Analytics 4 detetada" in o["evidence"] for o in opportunities)


def test_only_ua_detected_flags_migration_opportunity():
    tracking = _tracking(ua=_entry(detected=True, state="A disparar sem consentimento"))
    opportunities = derive_opportunities(tracking, [], OWNER_SERVICES)
    assert any("obsoleta" in o["evidence"] for o in opportunities)


def test_meta_pixel_present_but_silent_flags_configure_events_opportunity():
    tracking = _tracking(meta_pixel=_entry(detected=True, state="No código, sem disparo observado", events_observed=[]))
    opportunities = derive_opportunities(tracking, [], OWNER_SERVICES)
    assert any(o["service"] == "Meta Ads" and "Tag presente no código" in o["evidence"] and "sem disparo observado" in o["evidence"] for o in opportunities)


def test_firing_before_consent_flags_gdpr_review_opportunity():
    tracking = _tracking(
        ga4=_entry(detected=True, state="A disparar sem consentimento"),
        consent_interaction={"cmp": "OneTrust", "banner_found": True, "click_attempted": True, "click_had_effect": False},
    )
    opportunities = derive_opportunities(tracking, [], OWNER_SERVICES)
    assert any("RGPD" in o["pitch"] for o in opportunities)


def _fully_configured_tracking(**overrides):
    return _tracking(
        ga4=_entry(detected=True, state="A disparar só após consentimento"),
        gtm=_entry(detected=True, state="Detetado"),
        google_ads=_entry(detected=True, state="A disparar só após consentimento"),
        meta_pixel=_entry(detected=True, state="A disparar só após consentimento", events_observed=["Lead"]),
        microsoft_uet=_entry(detected=True, state="A disparar só após consentimento"),
        consent_mode={"detected": True, "state": "Detetado"},
        consent_interaction={"cmp": "OneTrust", "banner_found": True, "click_attempted": True, "click_had_effect": True},
        ecommerce_events_in_datalayer=["generate_lead"],
        **overrides,
    )


def test_fully_configured_tracking_yields_no_tracking_opportunities():
    opportunities = derive_opportunities(_fully_configured_tracking(), [], OWNER_SERVICES)
    assert opportunities == []


def test_no_banner_found_flags_a_medium_priority_opportunity_instead_of_gdpr_risk():
    """Secção C.3: sem nenhum banner de cookies, "tags a disparar antes do consentimento" não
    é um incumprimento de um mecanismo existente - é só a falta de uma CMP."""
    tracking = _tracking(ga4=_entry(detected=True, state="A disparar sem consentimento"))
    opportunities = derive_opportunities(tracking, [], OWNER_SERVICES)
    assert any("nenhum banner de consentimento" in o["evidence"] for o in opportunities)
    assert not any("jurista" in o["pitch"] for o in opportunities)


def test_global_gaps_become_copy_e_conversao_opportunities_capped_at_two():
    gaps = ["Falta CTA na página Sobre", "Sem preços indicados", "Sem garantia mencionada"]
    opportunities = derive_opportunities(_fully_configured_tracking(), gaps, OWNER_SERVICES)
    assert len(opportunities) == 2
    assert all(o["service"] == "Copy e conversão" for o in opportunities)


def test_opportunities_are_filtered_by_owner_services():
    opportunities = derive_opportunities(_tracking(), [], owner_services=["Google Ads"])
    assert all(o["service"] == "Google Ads" for o in opportunities)
    assert opportunities  # Google Ads rule still fires
