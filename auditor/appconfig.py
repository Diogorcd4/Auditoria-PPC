from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_llm_client(config: dict):
    from auditor.llm import OllamaClient, OpenAICompatClient

    backend = config["llm"]["backend"]
    if backend == "ollama":
        return OllamaClient(base_url=config["llm"]["ollama_url"], num_ctx=config["llm"].get("num_ctx", 8192))
    if backend == "openai_compatible":
        oc = config["llm"].get("openai_compatible", {})
        return OpenAICompatClient(base_url=oc.get("base_url", ""), api_key=oc.get("api_key", ""))
    raise ValueError(f"backend de LLM desconhecido em config.yaml: {backend}")


def model_for_task(config: dict, task: str) -> str:
    return config["llm"].get("tasks", {}).get(task, {}).get("model", "") or ""
