import json

import httpx
import pytest

from auditor.crawler import PageData
from auditor.keywords import (
    build_keywords,
    derive_seed_terms,
    fetch_autocomplete,
    fetch_observed_terms,
    heuristic_stage,
    infer_keywords_synthesis,
)
from auditor.llm.mock import MockLLMClient

FIXTURES = {"keywords": "keywords_synthesis.json"}


def _page(page_id="home", page_type="home", title=None, h1=None) -> PageData:
    return PageData(id=page_id, url=f"https://example.pt/{page_id}", type=page_type, title=title or "", h1=h1 or [])


def test_derive_seed_terms_prefers_home_h1_then_service_pages_then_titles():
    pages = [
        _page("home", "home", title="Clínica Sorriso — Início", h1=["Recupere o seu sorriso"]),
        _page("servico", "servico", title="Implantologia — Clínica Sorriso", h1=["Implantes dentários"]),
        _page("sobre", "sobre", title="Sobre Nós — Clínica Sorriso"),
    ]
    seeds = derive_seed_terms(pages)
    assert seeds[0] == "recupere o seu sorriso"
    assert "implantes dentários" in seeds
    assert "sobre nós" in seeds


def test_derive_seed_terms_caps_at_max_seeds():
    pages = [_page(f"p{i}", "servico", title=f"Serviço {i}", h1=[f"Serviço {i}"]) for i in range(20)]
    assert len(derive_seed_terms(pages, max_seeds=5)) == 5


@pytest.mark.parametrize(
    "term,expected",
    [
        ("quanto custa um implante dentário", "decisao"),
        ("melhor clínica dentária lisboa opiniões", "comparacao"),
        ("dentista perto de mim", "transacional_local"),
        ("porque dói o dente", "problema"),
        ("tratamento dentário sem dor", "solucao"),
    ],
)
def test_heuristic_stage_classification(term, expected):
    assert heuristic_stage(term) == expected


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_autocomplete_parses_the_suggest_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q")
        return httpx.Response(200, json=[query, [f"{query} a", f"{query} b"]])

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        suggestions = await fetch_autocomplete(client, "implantes")
    assert suggestions == ["implantes a", "implantes b"]


@pytest.mark.asyncio
async def test_fetch_autocomplete_returns_empty_list_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        suggestions = await fetch_autocomplete(client, "implantes")
    assert suggestions == []


@pytest.mark.asyncio
async def test_fetch_autocomplete_returns_empty_list_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        suggestions = await fetch_autocomplete(client, "implantes")
    assert suggestions == []


@pytest.mark.asyncio
async def test_fetch_observed_terms_dedupes_across_queries_and_caches_to_disk(tmp_path):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        query = request.url.params.get("q")
        return httpx.Response(200, json=[query, ["implantes dentários preço"]])

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        terms = await fetch_observed_terms(
            client, ["implantes"], delay_seconds=0, cache_dir=tmp_path, use_alphabet=False
        )

    assert terms == ["implantes dentários preço"]  # deduped even though every query "found" it
    cached_files = list(tmp_path.glob("*.json"))
    assert len(cached_files) == call_count["n"] > 0


@pytest.mark.asyncio
async def test_fetch_observed_terms_second_run_uses_cache_and_makes_no_requests(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q")
        return httpx.Response(200, json=[query, ["sugestão"]])

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        await fetch_observed_terms(client, ["seed"], delay_seconds=0, cache_dir=tmp_path, use_alphabet=False)

    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not hit the network on a cache hit")

    async with httpx.AsyncClient(transport=_mock_transport(failing_handler)) as client:
        terms_again = await fetch_observed_terms(client, ["seed"], delay_seconds=0, cache_dir=tmp_path, use_alphabet=False)

    assert terms_again == ["sugestão"]


@pytest.mark.asyncio
async def test_fetch_observed_terms_alphabet_sweep_adds_26_queries_per_seed(tmp_path):
    seen_queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q")
        seen_queries.append(query)
        return httpx.Response(200, json=[query, []])

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        await fetch_observed_terms(client, ["seed"], delay_seconds=0, cache_dir=tmp_path, use_alphabet=True)

    letter_queries = [q for q in seen_queries if q.startswith("seed ") and len(q) == len("seed x")]
    assert len(letter_queries) == 26


@pytest.mark.asyncio
async def test_infer_keywords_synthesis_returns_all_five_stages():
    llm = MockLLMClient(fixtures=FIXTURES)
    pages = [_page("home", "home", title="Clínica Sorriso")]
    result = await infer_keywords_synthesis(llm, seeds=["clínica sorriso"], observed=["clínica dentária lisboa"], pages=pages)
    assert result.inferred.decisao == ["marcar consulta avaliação gratuita"]
    assert result.negatives


@pytest.mark.asyncio
async def test_build_keywords_end_to_end_labels_observed_terms_with_a_stage(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q")
        # partilha "sorriso" com a semente "clínica sorriso" - passa o filtro de relevância
        # da secção A.4 (só ficam os termos observados com uma palavra em comum com a semente).
        return httpx.Response(200, json=[query, ["sorriso perto de mim"]])

    llm = MockLLMClient(fixtures=FIXTURES)
    pages = [_page("home", "home", title="Clínica Sorriso", h1=["Clínica Sorriso"])]

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        result = await build_keywords(llm, client, pages, cache_dir=tmp_path, use_alphabet=False)

    assert result["observed"][0]["term"] == "sorriso perto de mim"
    assert result["observed"][0]["stage"] == "transacional_local"
    assert result["inferred"]["decisao"] == ["marcar consulta avaliação gratuita"]
    assert result["negatives"]
    assert "Keyword Planner" in result["note"]
