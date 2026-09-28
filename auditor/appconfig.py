from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_dotenv_loaded = False


def _ensure_dotenv_loaded() -> None:
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv(ENV_PATH)
        _dotenv_loaded = True


def get_api_key() -> str:
    """The API key for any backend that needs one. Read from the environment (.env via
    python-dotenv, or a real environment variable) - never from config.yaml, which is
    committed to the repository (secção D.11 do briefing)."""
    _ensure_dotenv_loaded()
    return os.environ.get("AUDITOR_API_KEY", "")


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict, path: Path | str = CONFIG_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def _build_client_for_backend(config: dict, backend: str):
    from auditor.llm import OllamaClient, OpenAICompatClient

    if backend == "ollama":
        llm_cfg = config["llm"]
        return OllamaClient(
            base_url=llm_cfg["ollama_url"],
            num_ctx=llm_cfg.get("num_ctx", 4096),
            keep_alive=llm_cfg.get("ollama_keep_alive", "30m"),
        )
    if backend == "openai_compatible":
        oc = config["llm"].get("openai_compatible", {})
        return OpenAICompatClient(
            base_url=oc.get("base_url", GEMINI_BASE_URL),
            api_key=get_api_key(),
            requests_per_minute=config["llm"].get("requests_per_minute", 8),
            daily_limit=config["llm"].get("daily_limit", 450),
        )
    raise ValueError(f"backend de LLM desconhecido em config.yaml: {backend}")


def build_llm_client(config: dict):
    """The single global-backend client (`llm.backend`). Kept for callers - like the
    Definições "Testar ligação" endpoints - that only care about one backend at a time."""
    return _build_client_for_backend(config, config["llm"]["backend"])


def build_llm_client_for_task(config: dict, task: str):
    """Each pipeline task (analise, keywords, perfil, anuncios) can use its own backend and
    model - not just one global backend (secção D.12 do briefing). Falls back to the global
    `llm.backend` when a task has none configured, so Ollama keeps working as the default."""
    task_cfg = config["llm"].get("tasks", {}).get(task, {})
    backend = task_cfg.get("backend") or config["llm"]["backend"]
    return _build_client_for_backend(config, backend)


def model_for_task(config: dict, task: str) -> str:
    return config["llm"].get("tasks", {}).get(task, {}).get("model", "") or ""
