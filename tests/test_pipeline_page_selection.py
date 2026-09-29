"""Testes de integração para o bloco D do pedido de correcção: filtragem de páginas noutra
língua, exclusão de páginas legais/blog da Comunicação, e o limite de páginas analisadas."""

import httpx
import pytest

from auditor.llm.mock import MockLLMClient
from auditor.pipeline import PipelineConfig, run_pipeline

FIXTURES = {
    "analise": "analysis_page.json",
    "sintese": "site_synthesis.json",
    "keywords": "keywords_synthesis.json",
    "perfil": "site_profile.json",
}


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _keyword_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[request.url.params.get("q"), []])

    return httpx.MockTransport(handler)


def _build_site_with_legal_and_extra_pages(root, n_extra_pages=15):
    _write(
        root / "index.html",
        """<!doctype html><html><head><meta charset="utf-8"><title>Clínica Teste</title></head>
        <body>
        <nav>
          <a href="/servicos/">Serviços</a>
          <a href="/contacto/">Contacto</a>
          <a href="/politica-de-privacidade/">Privacidade</a>
        </nav>
        <h1>Bem-vindo à Clínica Teste</h1><p>Marque a sua consulta.</p><footer>Rodapé</footer>
        </body></html>""",
    )
    _write(root / "servicos" / "index.html", "<html><head><title>Serviços</title></head><body><h1>Os nossos serviços</h1></body></html>")
    _write(root / "contacto" / "index.html", "<html><head><title>Contacto</title></head><body><h1>Fale connosco</h1></body></html>")
    _write(
        root / "politica-de-privacidade" / "index.html",
        "<html><head><title>Política de Privacidade</title></head><body><h1>Política de Privacidade</h1><p>Termos legais.</p></body></html>",
    )
    for i in range(n_extra_pages):
        _write(root / "produtos" / str(i) / "index.html", f"<html><head><title>Produto {i}</title></head><body><h1>Produto {i}</h1></body></html>")


@pytest.mark.asyncio
async def test_comunicacao_excludes_legal_pages_and_caps_at_max_analyzed_pages(make_site_server, tmp_path, browser):
    _build_site_with_legal_and_extra_pages(tmp_path / "site", n_extra_pages=15)
    base_url = make_site_server(tmp_path / "site")
    llm = MockLLMClient(fixtures=FIXTURES)

    config = PipelineConfig(
        url=base_url,
        max_pages=25,  # o crawl descobre mais páginas do que as analisadas
        max_analyzed_pages=3,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / ".cache",
    )

    events = []
    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for event in run_pipeline(config, llm, browser=browser, http_client=http_client):
            events.append(event)

    crawl_done = next(e for e in events if e.step == "crawl" and e.status == "done")
    assert len(crawl_done.data["pages"]) > 3  # o crawl viu mais páginas do que o limite de análise

    comunicacao_done = next(e for e in events if e.step == "comunicacao" and e.status == "done")
    # nunca mais de max_analyzed_pages, e a página legal nunca está entre as analisadas.
    assert comunicacao_done.data["pages_total"] == 3
    analyzed_ids = {c["page_id"] for c in comunicacao_done.data["pages"]}
    assert "politica-de-privacidade" not in analyzed_ids


@pytest.mark.asyncio
async def test_crawl_drops_english_pages_when_the_portuguese_equivalent_exists(make_site_server, tmp_path, browser):
    root = tmp_path / "site"
    _write(root / "index.html", '<html><head><title>Início</title></head><body><nav><a href="/en/">EN</a></nav><h1>Bem-vindo</h1></body></html>')
    _write(root / "en" / "index.html", "<html><head><title>Home</title></head><body><h1>Welcome</h1></body></html>")
    base_url = make_site_server(root)
    llm = MockLLMClient(fixtures=FIXTURES)

    config = PipelineConfig(
        url=base_url,
        max_pages=5,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / ".cache",
    )

    events = []
    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for event in run_pipeline(config, llm, browser=browser, http_client=http_client):
            events.append(event)

    crawl_done = next(e for e in events if e.step == "crawl" and e.status == "done")
    urls = [p["url"] for p in crawl_done.data["pages"]]
    assert not any("/en" in u for u in urls)
