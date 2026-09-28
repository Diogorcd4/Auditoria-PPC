import pytest
from pydantic import BaseModel

from auditor.llm.base import LLMClient, LLMResponse
from auditor.llm.mock import MockLLMClient
from auditor.llm_json import LLMJsonError, generate_json


class Thing(BaseModel):
    name: str
    count: int


class _SequenceLLMClient(LLMClient):
    """Returns one canned response per call, in order - for exercising the retry path."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    async def generate(self, *, system, prompt, model="", json_schema=None, temperature=0.2):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return LLMResponse(content=response, model="seq", backend="sequence")

    async def list_models(self):
        return ["seq"]

    async def health_check(self):
        return True


@pytest.mark.asyncio
async def test_generate_json_succeeds_on_clean_json():
    client = _SequenceLLMClient(['{"name": "sorriso", "count": 3}'])
    result = await generate_json(client, task="thing", system="s", prompt="p", schema_model=Thing)
    assert result == Thing(name="sorriso", count=3)
    assert client.calls == 1


@pytest.mark.asyncio
async def test_generate_json_extracts_json_from_a_fenced_code_block():
    client = _SequenceLLMClient(['Aqui está:\n```json\n{"name": "x", "count": 1}\n```\nObrigado.'])
    result = await generate_json(client, task="thing", system="s", prompt="p", schema_model=Thing)
    assert result == Thing(name="x", count=1)


@pytest.mark.asyncio
async def test_generate_json_retries_once_after_invalid_json_then_succeeds():
    client = _SequenceLLMClient(["isto não é JSON", '{"name": "y", "count": 2}'])
    result = await generate_json(client, task="thing", system="s", prompt="p", schema_model=Thing, max_retries=2)
    assert result == Thing(name="y", count=2)
    assert client.calls == 2


@pytest.mark.asyncio
async def test_generate_json_raises_after_exhausting_retries():
    client = _SequenceLLMClient(["nope", "still nope", "nope again"])
    with pytest.raises(LLMJsonError):
        await generate_json(client, task="thing", system="s", prompt="p", schema_model=Thing, max_retries=2)
    assert client.calls == 3


@pytest.mark.asyncio
async def test_generate_json_tags_the_system_prompt_with_the_task_marker():
    seen_systems = []

    class _Spy(_SequenceLLMClient):
        async def generate(self, *, system, prompt, model="", json_schema=None, temperature=0.2):
            seen_systems.append(system)
            return await super().generate(system=system, prompt=prompt, model=model, json_schema=json_schema, temperature=temperature)

    client = _Spy(['{"name": "z", "count": 9}'])
    await generate_json(client, task="perfil", system="Analisa isto.", prompt="p", schema_model=Thing)
    assert seen_systems[0].startswith("[TASK:perfil]")


@pytest.mark.asyncio
async def test_generate_json_works_with_mock_llm_client_fixture_routing(tmp_path):
    (tmp_path / "thing.json").write_text('{"name": "fixture", "count": 7}', encoding="utf-8")
    client = MockLLMClient(fixtures={"analise": "thing.json"}, fixtures_dir=tmp_path)
    result = await generate_json(client, task="analise", system="s", prompt="p", schema_model=Thing)
    assert result == Thing(name="fixture", count=7)
