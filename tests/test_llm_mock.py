import json

import pytest

from auditor.llm import MockLLMClient


@pytest.mark.asyncio
async def test_mock_client_returns_fixture_selected_by_task_marker(tmp_path):
    fixture_path = tmp_path / "analise.json"
    fixture_path.write_text(json.dumps({"ok": True}), encoding="utf-8")

    client = MockLLMClient(fixtures={"analise": "analise.json"}, fixtures_dir=tmp_path)
    response = await client.generate(system="[TASK:analise] Analisa a página.", prompt="conteúdo da página")

    assert json.loads(response.content) == {"ok": True}
    assert response.backend == "mock"
    assert client.calls[0]["task"] == "analise"


@pytest.mark.asyncio
async def test_mock_client_falls_back_to_default_when_task_has_no_mapping(tmp_path):
    (tmp_path / "default_response.json").write_text("{}", encoding="utf-8")
    client = MockLLMClient(fixtures_dir=tmp_path)

    response = await client.generate(system="sem marcador de tarefa", prompt="x")

    assert response.content == "{}"


@pytest.mark.asyncio
async def test_mock_client_health_check_and_list_models_never_touch_the_network():
    client = MockLLMClient()
    assert await client.health_check() is True
    assert await client.list_models() == ["mock-model"]
