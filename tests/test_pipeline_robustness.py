"""Testes para o reforço de robustez do pipeline (secções A/B do pedido de correcção):
erro nunca vazio, falha de uma página isolada não arrasta a Comunicação toda, e um passo
que depende doutro falhado nunca rebenta com um KeyError em bruto."""

import httpx
import pytest

from auditor.llm.mock import MockLLMClient
from auditor.pipeline import DependencyNotMetError, PipelineConfig, _format_error, run_pipeline


def test_format_error_names_the_exception_class_when_the_message_is_empty():
    message = _format_error(TimeoutError())
    assert message.startswith("TimeoutError:")
    assert "demorou demasiado tempo" in message


def test_format_error_keeps_a_real_message_untouched():
    assert _format_error(ValueError("algo específico correu mal")) == "algo específico correu mal"


class _FailFirstNLLMClient(MockLLMClient):
    """Falha nas primeiras N chamadas à tarefa 'analise', depois comporta-se como o Mock normal."""

    def __init__(self, *, fail_calls: int, **kwargs):
        super().__init__(**kwargs)
        self.fail_calls = fail_calls
        self._analise_calls = 0

    async def generate(self, *, system, prompt, model="", json_schema=None, temperature=0.2):
        if self._extract_task(system) == "analise":
            self._analise_calls += 1
            if self._analise_calls <= self.fail_calls:
                raise httpx.ReadTimeout("")
        return await super().generate(system=system, prompt=prompt, model=model, json_schema=json_schema, temperature=temperature)


FIXTURES = {
    "analise": "analysis_page.json",
    "sintese": "site_synthesis.json",
    "keywords": "keywords_synthesis.json",
    "perfil": "site_profile.json",
}


def _write_site(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        """<!doctype html><html><head><meta charset="utf-8"><title>Clínica Teste</title></head>
        <body><nav><a href="/sobre/">Sobre</a></nav><h1>Bem-vindo</h1><p>Marque a sua consulta.</p><footer>Rodapé</footer></body></html>""",
        encoding="utf-8",
    )
    (root / "sobre").mkdir(exist_ok=True)
    (root / "sobre" / "index.html").write_text(
        "<html><head><title>Sobre</title></head><body><h1>Sobre nós</h1></body></html>",
        encoding="utf-8",
    )


def _keyword_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[request.url.params.get("q"), []])

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_comunicacao_tolerates_one_failing_page_and_still_reaches_sintese(make_site_server, tmp_path, browser):
    _write_site(tmp_path / "site")
    base_url = make_site_server(tmp_path / "site")
    # duas páginas no site; a primeira chamada a "analise" falha, a segunda tem sucesso.
    llm = _FailFirstNLLMClient(fail_calls=1, fixtures=FIXTURES)

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

    comunicacao_done = next(e for e in events if e.step == "comunicacao" and e.status == "done")
    assert comunicacao_done.data["pages_ok"] < comunicacao_done.data["pages_total"]
    assert len(comunicacao_done.data["failed_pages"]) == 1

    # a Comunicação não falhou de todo (havia pelo menos uma página bem sucedida), por isso
    # a Síntese consegue continuar em vez de rebentar com uma dependência em falta.
    sintese_events = [e for e in events if e.step == "sintese"]
    assert any(e.status == "done" for e in sintese_events)


@pytest.mark.asyncio
async def test_sintese_reports_a_clear_dependency_error_when_every_page_fails(make_site_server, tmp_path, browser):
    _write_site(tmp_path / "site")
    base_url = make_site_server(tmp_path / "site")
    # falha em todas as páginas -> a Comunicação em si não produz nenhum resultado utilizável.
    llm = _FailFirstNLLMClient(fail_calls=999, fixtures=FIXTURES)

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

    comunicacao_error = next(e for e in events if e.step == "comunicacao" and e.status == "error")
    assert comunicacao_error.error  # nunca vazio

    sintese_error = next(e for e in events if e.step == "sintese" and e.status == "error")
    # nunca deve aparecer como um KeyError('comunicacao') em bruto - tem de ser uma frase legível
    assert "'comunicacao'" not in sintese_error.error
    assert "Comunicação" in sintese_error.error
