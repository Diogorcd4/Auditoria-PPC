"""Testes para o bloco C do pedido de correcção: consistência entre "detected" e "state" no
relatório de tracking, e a informação de consentimento/CMP exposta no relatório."""

from auditor.pipeline import _tracking_platforms_detected, _tracking_platforms_firing
from auditor.tracking import _RequestLog, _build_report, merge_reports

GTM_HTML = '<html><head><script src="https://www.googletagmanager.com/gtag/js?id=G-ABC123"></script></head><body></body></html>'
RUNTIME_WITH_DATALAYER = {"dataLayerLength": 3, "gtmContainers": [], "hasFbq": False, "hasUetq": False, "hasGa": False}
EMPTY_RUNTIME = {"dataLayerLength": None, "gtmContainers": [], "hasFbq": False, "hasUetq": False, "hasGa": False}


def test_a_tag_loaded_only_via_gtm_dataLayer_is_never_detected_false():
    """Causa do bug relatado: antes desta correcção, "detected" só olhava para IDs extraídos
    directamente do HTML/pedidos de rede - uma tag carregada só via GTM (gtag.js + dataLayer,
    sem gtag('config',...) inline) tinha state="No código, sem disparo observado" mas
    detected=False, o que gerava a oportunidade errada "Nenhuma tag ... detetada" (secção C.1)."""
    log = _RequestLog()
    loaded = {"gtag": ["https://www.googletagmanager.com/gtag/js?id=G-ABC123"]}
    report = _build_report(GTM_HTML, RUNTIME_WITH_DATALAYER, log, loaded)

    assert report["ga4"]["state"] == "No código, sem disparo observado"
    assert report["ga4"]["detected"] is True  # nunca False quando o estado não é "Não detetado"


def test_detected_is_always_exactly_state_is_not_nao_detetado():
    log = _RequestLog()
    not_loaded = _build_report("<html></html>", EMPTY_RUNTIME, log, {})
    for key in ("ga4", "ua", "google_ads", "meta_pixel", "microsoft_uet"):
        assert not_loaded[key]["detected"] == (not_loaded[key]["state"] != "Não detetado")
        assert not_loaded[key]["detected"] is False


def test_silent_tag_evidence_names_the_id_when_available():
    log = _RequestLog()
    loaded = {"gtag": ["https://www.googletagmanager.com/gtag/js?id=G-ABC123"]}
    report = _build_report(GTM_HTML, RUNTIME_WITH_DATALAYER, log, loaded)
    evidence = report["ga4"]["evidence"]
    assert evidence
    assert "confirmar se depende de um consentimento não reconhecido" in evidence[0]


def test_report_exposes_consent_interaction_with_cmp_banner_and_click_effect():
    log = _RequestLog()
    log.click_attempted = True
    log.consent_given = True
    log.record("https://www.google-analytics.com/g/collect?v=2&tid=G-ABC123")  # depois do "clique"
    html = '<div id="onetrust-banner-sdk"></div><div id="onetrust-accept-btn-handler"></div>'
    report = _build_report(html, EMPTY_RUNTIME, log, {})

    ci = report["consent_interaction"]
    assert ci["cmp"] == "OneTrust"
    assert ci["banner_found"] is True
    assert ci["click_attempted"] is True
    assert ci["click_had_effect"] is True  # houve pedidos depois do clique


def test_report_says_no_banner_was_found_when_none_exists():
    log = _RequestLog()
    report = _build_report("<html><body>Sem CMP nenhuma aqui.</body></html>", EMPTY_RUNTIME, log, {})
    ci = report["consent_interaction"]
    assert ci["banner_found"] is False
    assert ci["click_attempted"] is False
    assert ci["click_had_effect"] is False


def test_merge_reports_keeps_the_consent_interaction_of_the_page_where_a_banner_was_found():
    log_no_banner = _RequestLog()
    report_no_banner = _build_report("<html></html>", EMPTY_RUNTIME, log_no_banner, {})

    log_with_banner = _RequestLog()
    log_with_banner.click_attempted = True
    log_with_banner.consent_given = True
    html_with_banner = '<div id="onetrust-banner-sdk"></div>'
    report_with_banner = _build_report(html_with_banner, EMPTY_RUNTIME, log_with_banner, {})

    merged = merge_reports([report_no_banner, report_with_banner])
    assert merged["consent_interaction"]["banner_found"] is True
    assert merged["consent_interaction"]["cmp"] == "OneTrust"


def test_tracking_platforms_detected_counts_code_presence_or_firing():
    tracking = {
        "ga4": {"detected": True, "state": "No código, sem disparo observado"},
        "gtm": {"detected": True, "state": "Detetado"},
        "google_ads": {"detected": False, "state": "Não detetado"},
        "meta_pixel": {"detected": True, "state": "A disparar sem consentimento"},
        "microsoft_uet": {"detected": False, "state": "Não detetado"},
        "consent_mode": {"detected": True, "state": "Detetado"},
    }
    assert _tracking_platforms_detected(tracking) == 4  # ga4, gtm, meta_pixel, consent_mode


def test_tracking_platforms_firing_only_counts_states_that_actually_fire():
    tracking = {
        "ga4": {"detected": True, "state": "No código, sem disparo observado"},
        "google_ads": {"detected": True, "state": "A disparar só após consentimento"},
        "meta_pixel": {"detected": True, "state": "A disparar sem consentimento"},
        "microsoft_uet": {"detected": False, "state": "Não detetado"},
    }
    assert _tracking_platforms_firing(tracking) == 2  # google_ads e meta_pixel disparam; ga4 não
