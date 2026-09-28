import pytest

from auditor.tracking import detect_tracking, merge_reports

TRACKER_SCRIPT_HOSTS = ("googletagmanager.com", "connect.facebook.net", "bat.bing.com", "google-analytics.com/analytics.js")


def _stub_body_for(url: str) -> str:
    # Minimal functional stand-ins, so a page that calls fbq(...)/gtag(...) after these
    # "load" doesn't crash on a ReferenceError before it gets to actually fire anything.
    if "connect.facebook.net" in url:
        return "window.fbq = window.fbq || function(){ (window.fbq.q = window.fbq.q || []).push(arguments); };"
    if "bat.bing.com/bat.js" in url:
        return "window.uetq = window.uetq || [];"
    return "/* stub */"


async def _stub_tracker_scripts(page):
    """Fulfill requests for the tracker *library* files themselves with a harmless stub, so
    tests never touch the real internet. Registered before detect_tracking's own route, so
    it runs as the fallback target once detect_tracking's handler calls route.fallback().
    """

    async def handler(route):
        await route.fulfill(status=200, content_type="application/javascript", body=_stub_body_for(route.request.url))

    await page.route(lambda url: any(host in url for host in TRACKER_SCRIPT_HOSTS), handler)


def _html(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"


async def _serve_and_detect(page, make_site_server, tmp_path, body: str) -> dict:
    (tmp_path / "index.html").write_text(_html(body), encoding="utf-8")
    base_url = make_site_server(tmp_path)
    await _stub_tracker_scripts(page)
    return await detect_tracking(page, f"{base_url}/index.html")


@pytest.mark.asyncio
async def test_ga4_fires_without_waiting_for_consent(page, make_site_server, tmp_path):
    body = """
    <script src="https://www.googletagmanager.com/gtag/js?id=G-TESTFIRE"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-TESTFIRE');
      fetch('https://www.google-analytics.com/g/collect?v=2&tid=G-TESTFIRE&cid=1', {mode:'no-cors'});
    </script>
    <button id="onetrust-accept-btn-handler">Aceitar todos</button>
    """
    report = await _serve_and_detect(page, make_site_server, tmp_path, body)

    assert report["ga4"]["detected"] is True
    assert report["ga4"]["id"] == "G-TESTFIRE"
    assert report["ga4"]["state"] == "A disparar sem consentimento"
    assert report["ga4"]["evidence"]
    assert report["cmp"]["name"] == "OneTrust"


@pytest.mark.asyncio
async def test_ga4_fires_only_after_consent(page, make_site_server, tmp_path):
    body = """
    <script src="https://www.googletagmanager.com/gtag/js?id=G-TESTWAIT"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-TESTWAIT');
    </script>
    <button id="onetrust-accept-btn-handler"
            onclick="fetch('https://www.google-analytics.com/g/collect?v=2&tid=G-TESTWAIT&cid=1',{mode:'no-cors'})">
      Aceitar todos
    </button>
    """
    report = await _serve_and_detect(page, make_site_server, tmp_path, body)

    assert report["ga4"]["state"] == "A disparar só após consentimento"
    assert report["ga4"]["id"] == "G-TESTWAIT"


@pytest.mark.asyncio
async def test_ga4_in_code_without_any_observed_firing(page, make_site_server, tmp_path):
    body = """
    <script src="https://www.googletagmanager.com/gtag/js?id=G-TESTSILENT"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-TESTSILENT');
    </script>
    <button id="onetrust-accept-btn-handler">Aceitar todos</button>
    """
    report = await _serve_and_detect(page, make_site_server, tmp_path, body)

    assert report["ga4"]["state"] == "No código, sem disparo observado"
    assert report["ga4"]["id"] == "G-TESTSILENT"


@pytest.mark.asyncio
async def test_ga4_not_detected_on_a_clean_page(page, make_site_server, tmp_path):
    report = await _serve_and_detect(page, make_site_server, tmp_path, "<h1>Página sem tracking</h1>")

    assert report["ga4"]["detected"] is False
    assert report["ga4"]["id"] is None
    assert report["ga4"]["state"] == "Não detetado"
    assert report["cmp"]["name"] == "Não detetado"


@pytest.mark.asyncio
async def test_meta_pixel_uet_gtm_consent_mode_and_cmp_all_detected_together(page, make_site_server, tmp_path):
    body = """
    <script src="https://www.googletagmanager.com/gtm.js?id=GTM-TESTCONT"></script>
    <script src="https://connect.facebook.net/en_US/fbevents.js"></script>
    <script src="https://bat.bing.com/bat.js"></script>
    <script>
      fbq('init', '1234567890');
      fetch('https://www.facebook.com/tr?id=1234567890&ev=PageView', {mode:'no-cors'});
      fetch('https://bat.bing.com/action?ti=555111&Ver=2', {mode:'no-cors'});
      fetch('https://www.google-analytics.com/g/collect?v=2&tid=G-TESTGCM&cid=1&gcs=G111&gcd=13l3l3l3l3l1', {mode:'no-cors'});
    </script>
    <div id="onetrust-banner-sdk">
      <button id="onetrust-accept-btn-handler">Aceitar todos</button>
    </div>
    """
    report = await _serve_and_detect(page, make_site_server, tmp_path, body)

    assert report["gtm"]["detected"] is True
    assert report["gtm"]["id"] == "GTM-TESTCONT"
    assert report["meta_pixel"]["detected"] is True
    assert report["meta_pixel"]["id"] == "1234567890"
    assert report["meta_pixel"]["state"] == "A disparar sem consentimento"
    assert report["microsoft_uet"]["detected"] is True
    assert report["microsoft_uet"]["state"] == "A disparar sem consentimento"
    assert report["consent_mode"]["detected"] is True
    assert report["cmp"]["name"] == "OneTrust"


@pytest.mark.asyncio
async def test_never_sends_real_collect_requests_to_the_audited_site(page, make_site_server, tmp_path):
    """The privacy requirement: collect/track/action calls are captured as evidence and
    aborted, never actually delivered - so an audit never pollutes the site's own Analytics,
    Ads or Pixel data."""
    seen_collect_requests = []
    page.on("request", lambda req: seen_collect_requests.append(req.url) if "g/collect" in req.url else None)

    body = """
    <script>
      fetch('https://www.google-analytics.com/g/collect?v=2&tid=G-TESTABORT&cid=1', {mode:'no-cors'})
        .then(() => { window.__collectResolved = true; })
        .catch(() => { window.__collectResolved = 'error'; });
    </script>
    """
    report = await _serve_and_detect(page, make_site_server, tmp_path, body)

    assert report["ga4"]["state"] == "A disparar sem consentimento"
    # the request was observed (for evidence) but Playwright aborted it before it reached the network
    assert any("G-TESTABORT" in u for u in seen_collect_requests)


def test_merge_reports_picks_the_most_revealing_state_and_combines_evidence():
    report_a = {
        "ga4": {"platform": "Google Analytics 4", "detected": True, "id": "G-A", "state": "No código, sem disparo observado", "evidence": []},
        "ua": {"platform": "Universal Analytics", "detected": False, "id": None, "state": "Não detetado", "evidence": []},
        "gtm": {"platform": "Google Tag Manager", "detected": False, "id": None, "state": "Não detetado", "evidence": []},
        "google_ads": {"platform": "Google Ads", "detected": False, "id": None, "state": "Não detetado", "evidence": []},
        "meta_pixel": {"platform": "Meta Pixel", "detected": False, "id": None, "state": "Não detetado", "evidence": []},
        "microsoft_uet": {"platform": "Microsoft Advertising (UET)", "detected": False, "id": None, "state": "Não detetado", "evidence": []},
        "consent_mode": {"detected": False, "state": "Não detetado"},
        "cmp": {"name": "Não detetado", "cookie_banner_detected": False},
        "extras": [{"platform": "Hotjar", "detected": False, "state": "Não detetado"}],
        "ecommerce_events_in_datalayer": [],
        "not_visible_note": "nota",
    }
    report_b = {
        **report_a,
        "ga4": {"platform": "Google Analytics 4", "detected": True, "id": "G-A", "state": "A disparar sem consentimento", "evidence": ["Pedido de rede para x"]},
        "extras": [{"platform": "Hotjar", "detected": True, "state": "Detetado"}],
        "ecommerce_events_in_datalayer": ["purchase"],
    }

    merged = merge_reports([report_a, report_b])

    assert merged["ga4"]["state"] == "A disparar sem consentimento"
    assert merged["ga4"]["evidence"] == ["Pedido de rede para x"]
    assert merged["extras"][0] == {"platform": "Hotjar", "detected": True, "state": "Detetado"}
    assert merged["ecommerce_events_in_datalayer"] == ["purchase"]


def test_merge_reports_single_report_is_returned_unchanged():
    report = {"ga4": {"state": "Não detetado"}}
    assert merge_reports([report]) is report
