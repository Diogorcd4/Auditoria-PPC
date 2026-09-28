import pytest

from auditor.crawler import classify_page_type, crawl_site, normalize_url, should_skip_url


def test_normalize_url_drops_query_string_fragment_and_trailing_slash():
    assert normalize_url("https://Example.com/Servicos/?utm_source=ads#topo") == "https://example.com/Servicos"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_should_skip_url_flags_files_and_private_areas():
    assert should_skip_url("https://example.com/catalogo.pdf")
    assert should_skip_url("https://example.com/carrinho")
    assert should_skip_url("https://example.com/wp-admin/edit.php")
    assert not should_skip_url("https://example.com/servicos/implantologia")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/", "home"),
        ("https://example.com/servicos/implantologia", "servico"),
        ("https://example.com/precos", "precos"),
        ("https://example.com/sobre-nos", "sobre"),
        ("https://example.com/contacto", "contacto"),
        ("https://example.com/blog/artigo-1", "blog"),
        ("https://example.com/qualquer-coisa", "outro"),
    ],
)
def test_classify_page_type_from_url_keywords(url, expected):
    assert classify_page_type(url) == expected


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_site(root):
    _write(
        root / "index.html",
        """<!doctype html><html><head><title>Home</title>
        <meta name="description" content="Página inicial"></head>
        <body>
        <nav><a href="/servicos/">Serviços</a><a href="/sobre/">Sobre</a><a href="/contacto/">Contacto</a></nav>
        <h1>Bem-vindo</h1>
        <a href="/blog/artigo-1/">Ler o blog</a>
        <a href="/privado/painel/">Painel privado</a>
        <a href="/login/">Entrar</a>
        <footer><a href="/servicos/">Serviços</a></footer>
        </body></html>""",
    )
    _write(root / "robots.txt", "User-agent: *\nDisallow: /privado/\n")
    _write(
        root / "servicos" / "index.html",
        "<html><head><title>Serviços</title></head><body><h1>Os nossos serviços</h1></body></html>",
    )
    _write(
        root / "sobre" / "index.html",
        "<html><head><title>Sobre</title></head><body><h1>Sobre nós</h1></body></html>",
    )
    _write(
        root / "contacto" / "index.html",
        "<html><head><title>Contacto</title></head><body><h1>Fale connosco</h1></body></html>",
    )
    _write(
        root / "blog" / "artigo-1" / "index.html",
        "<html><head><title>Artigo</title></head><body><h1>Um artigo</h1></body></html>",
    )
    _write(
        root / "privado" / "painel" / "index.html",
        "<html><head><title>Privado</title></head><body><h1>Não deveria ser visitado</h1></body></html>",
    )
    _write(
        root / "login" / "index.html",
        "<html><head><title>Login</title></head><body><h1>Não deveria ser visitado</h1></body></html>",
    )


@pytest.mark.asyncio
async def test_crawl_discovers_pages_and_skips_private_and_disallowed_paths(make_site_server, tmp_path, browser):
    _build_site(tmp_path)
    base_url = make_site_server(tmp_path)

    result = await crawl_site(base_url, max_pages=10, delay_seconds=0, cache_dir=tmp_path / ".cache", browser=browser)

    urls = {p.url for p in result.pages}
    assert f"{base_url}/" in urls or any(u.rstrip("/") == base_url for u in urls)
    assert any(u.endswith("/servicos") for u in urls)
    assert any(u.endswith("/sobre") for u in urls)
    assert any(u.endswith("/contacto") for u in urls)
    assert any(u.endswith("/blog/artigo-1") for u in urls)
    assert not any("/privado/" in u for u in urls)
    assert not any("/login" in u for u in urls)

    types_by_url = {p.url: p.type for p in result.pages}
    home_url = next(u for u in urls if u.endswith("/") or u == base_url)
    assert types_by_url[home_url] == "home"


@pytest.mark.asyncio
async def test_crawl_respects_max_pages(make_site_server, tmp_path, browser):
    _build_site(tmp_path)
    base_url = make_site_server(tmp_path)

    result = await crawl_site(base_url, max_pages=2, delay_seconds=0, cache_dir=tmp_path / ".cache", browser=browser)

    assert len(result.pages) == 2


@pytest.mark.asyncio
async def test_crawl_caches_pages_to_disk(make_site_server, tmp_path, browser):
    _build_site(tmp_path)
    base_url = make_site_server(tmp_path)
    cache_dir = tmp_path / ".cache"

    await crawl_site(base_url, max_pages=10, delay_seconds=0, cache_dir=cache_dir, browser=browser)

    cached_files = list(cache_dir.rglob("*.json"))
    assert len(cached_files) > 0


@pytest.mark.asyncio
async def test_crawl_extracts_structured_content(make_site_server, tmp_path, browser):
    _write(
        tmp_path / "index.html",
        """<!doctype html><html><head><meta charset="utf-8"><title>Clínica X</title>
        <meta name="description" content="Clínica de excelência">
        <script type="application/ld+json">{"@type": "MedicalClinic", "name": "Clínica X"}</script>
        </head>
        <body class="wp-content">
        <nav><a href="/nav-link/">Nav</a></nav>
        <h1>Título principal</h1>
        <h2>Subtítulo</h2>
        <p>Consulta a partir de 49€ por sessão. Contacte-nos: geral@clinicax.pt</p>
        <div class="testimonial">Adorei o atendimento, recomendo!</div>
        <form><input type="text" name="nome" placeholder="O seu nome"><input type="submit" value="Enviar"></form>
        <button class="btn">Marcar consulta</button>
        <footer>Rodapé não deve entrar no texto principal</footer>
        </body></html>""",
    )
    base_url = make_site_server(tmp_path)

    result = await crawl_site(base_url, max_pages=1, delay_seconds=0, cache_dir=tmp_path / ".cache", browser=browser)

    page = result.pages[0]
    assert page.title == "Clínica X"
    assert page.meta_description == "Clínica de excelência"
    assert page.h1 == ["Título principal"]
    assert page.h2 == ["Subtítulo"]
    assert "Marcar consulta" in page.ctas
    assert page.forms[0]["field_count"] == 1
    assert any("Adorei" in t for t in page.testimonials)
    assert "49€" in page.prices or "49€" in "".join(page.prices)
    assert page.contacts.get("email") == "geral@clinicax.pt"
    assert "MedicalClinic" in page.schema_types
    assert page.platform == "WordPress"
    assert "Rodapé" not in page.main_text
    assert "Nav" not in page.main_text
