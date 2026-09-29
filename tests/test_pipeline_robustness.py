"""Testes para o reforço de robustez do pipeline (secções A/B do pedido de correcção):
erro nunca vazio, falha de uma página isolada não arrasta a Comunicação toda, e um passo
que depende doutro falhado nunca rebenta com um KeyError em bruto."""

import httpx
import pytest

from auditor.llm.mock import MockLLMClient
from auditor.llm.openai_compat import OpenAICompatClient
from auditor.pipeline import DependencyNotMetError, PipelineConfig, _format_error, _require_model, run_audit_sync, run_pipeline


def test_format_error_names_the_exception_class_when_the_message_is_empty():
    message = _format_error(TimeoutError())
    assert message.startswith("TimeoutError:")
    assert "demorou demasiado tempo" in message


def test_format_error_keeps_a_real_message_untouched():
    assert _format_error(ValueError("algo específico correu mal")) == "algo específico correu mal"


def test_require_model_raises_a_clear_pt_pt_message_naming_the_task_for_openai_compatible():
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k")
    with pytest.raises(RuntimeError) as excinfo:
        _require_model(client, "analise", "")
    assert "analise" in str(excinfo.value)
    assert "config.yaml" in str(excinfo.value)


def test_require_model_does_nothing_for_ollama_or_a_non_empty_model():
    client = OpenAICompatClient(base_url="https://example.test/v1", api_key="k")
    _require_model(client, "analise", "gemini-flash-lite-latest")  # não deve levantar nada
    _require_model(MockLLMClient(), "analise", "")  # backends que não exigem modelo explícito


@pytest.mark.asyncio
async def test_comunicacao_never_calls_the_api_when_the_task_model_is_empty(make_site_server, tmp_path, browser, monkeypatch):
    """Reproduz a causa-raiz relatada: `python -m auditor audit <url> --fast` falhava com
    HTTP 400 'model is not specified' em TODOS os pedidos porque model_for_task chegava vazio
    ao openai_compatible. Agora falha logo, em Python, sem nunca chegar a fazer um pedido."""
    _write_site(tmp_path / "site")
    base_url = make_site_server(tmp_path / "site")
    llm = OpenAICompatClient(base_url="https://example.test/v1", api_key="k")

    async def _never_call_the_api(*args, **kwargs):
        raise AssertionError("não devia ter sido feito nenhum pedido HTTP com o modelo vazio")

    monkeypatch.setattr(OpenAICompatClient, "generate", _never_call_the_api)

    config = PipelineConfig(
        url=base_url,
        max_pages=5,
        delay_seconds=0,
        keyword_delay_seconds=0,
        keyword_use_alphabet=False,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / ".cache",
        model_for_task={},  # exactamente o bug: nenhum modelo configurado para nenhuma tarefa
    )

    events = []
    async with httpx.AsyncClient(transport=_keyword_mock_transport()) as http_client:
        async for event in run_pipeline(config, llm, browser=browser, http_client=http_client):
            events.append(event)

    comunicacao_error = next(e for e in events if e.step == "comunicacao" and e.status == "error")
    assert "analise" in comunicacao_error.error
    assert "model is not specified" not in comunicacao_error.error


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


def test_run_audit_sync_passes_model_for_task_from_config_to_the_pipeline(monkeypatch):
    """Regressão exacta do bug relatado: `python -m auditor audit <url> --fast` construía o
    PipelineConfig sem `model_for_task`, por isso todas as tarefas recebiam model="" mesmo
    com o modelo preenchido no config.yaml (secção 1). Isto verifica que run_audit_sync
    passa sempre o dicionário de modelos ao pipeline, sem chegar a correr nada real."""
    captured_config = {}

    async def fake_run_pipeline(config, llm, **kwargs):
        captured_config["config"] = config
        return
        yield  # pragma: no cover - torna esta função um gerador assíncrono, nunca executado

    def fake_load_config():
        return {
            "crawl": {"delay_seconds": 0, "respect_robots": True},
            "tracking": {"pages": 4},
            "llm": {
                "max_page_chars": 4000,
                "tasks": {
                    "analise": {"model": "gemini-flash-lite-latest"},
                    "keywords": {"model": "gemini-flash-lite-latest"},
                    "perfil": {"model": "gemini-flash-lite-latest"},
                    "anuncios": {"model": "gemini-flash-lite-latest"},
                },
            },
            "owner_services": [],
        }

    monkeypatch.setattr("auditor.appconfig.load_config", fake_load_config)
    monkeypatch.setattr("auditor.appconfig.build_llm_client_for_task", lambda config, task: object())
    monkeypatch.setattr("auditor.pipeline.run_pipeline", fake_run_pipeline)

    run_audit_sync("example.pt", fast=True)

    model_for_task = captured_config["config"].model_for_task
    assert model_for_task == {
        "analise": "gemini-flash-lite-latest",
        "keywords": "gemini-flash-lite-latest",
        "perfil": "gemini-flash-lite-latest",
        "anuncios": "gemini-flash-lite-latest",
    }
    assert captured_config["config"].fast is True
