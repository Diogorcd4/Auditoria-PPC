"""Testes para a camada de definições persistentes (secção 7): config.yaml no repositório
fica só com valores por defeito, e as definições reais do utilizador vivem em
config.local.yaml (fora do Git), fundidas por cima no arranque."""

from pathlib import Path

import yaml

from auditor.appconfig import load_config, save_config


def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def test_load_config_returns_the_base_file_when_there_is_no_local_override(tmp_path):
    base = tmp_path / "config.yaml"
    _write_yaml(base, {"app_name": "Auditor", "llm": {"backend": "ollama"}})

    config = load_config(base)
    assert config["app_name"] == "Auditor"
    assert config["llm"]["backend"] == "ollama"


def test_load_config_merges_config_local_yaml_on_top_of_the_base_file(tmp_path):
    base = tmp_path / "config.yaml"
    _write_yaml(base, {"app_name": "Auditor", "llm": {"backend": "ollama", "num_ctx": 4096}, "crawl": {"max_pages": 25}})
    local = tmp_path / "config.local.yaml"
    _write_yaml(local, {"llm": {"backend": "openai_compatible"}})

    config = load_config(base)
    # o backend foi substituído pela definição local...
    assert config["llm"]["backend"] == "openai_compatible"
    # ...mas as restantes chaves de "llm" (não tocadas no ficheiro local) sobrevivem, e
    # "crawl" (ausente do ficheiro local) também sobrevive - é um merge, não uma substituição.
    assert config["llm"]["num_ctx"] == 4096
    assert config["crawl"]["max_pages"] == 25


def test_save_config_never_touches_the_repository_config_yaml(tmp_path):
    base = tmp_path / "config.yaml"
    _write_yaml(base, {"app_name": "Auditor"})
    original_bytes = base.read_bytes()

    save_config({"app_name": "Nome Novo"}, base)

    assert base.read_bytes() == original_bytes  # config.yaml do repositório nunca é escrito
    assert (tmp_path / "config.local.yaml").exists()
    assert load_config(base)["app_name"] == "Nome Novo"


def test_settings_survive_a_fresh_copy_of_config_yaml_as_long_as_config_local_yaml_stays(tmp_path):
    """Simula trocar o zip do projecto: config.yaml é substituído por uma cópia nova (por
    defeito), mas config.local.yaml (nunca enviado com o zip) fica no mesmo sítio."""
    base = tmp_path / "config.yaml"
    _write_yaml(base, {"llm": {"backend": "openai_compatible", "tasks": {"analise": {"model": "gemini-flash-lite-latest"}}}})
    save_config({"llm": {"backend": "openai_compatible", "tasks": {"analise": {"model": "modelo-do-utilizador"}}}}, base)

    # "novo zip": o config.yaml de defeito é reescrito, mas o local.yaml mantém-se.
    _write_yaml(base, {"llm": {"backend": "openai_compatible", "tasks": {"analise": {"model": "gemini-flash-lite-latest"}}}})

    config = load_config(base)
    assert config["llm"]["tasks"]["analise"]["model"] == "modelo-do-utilizador"


def test_config_round_trips_accented_pt_pt_text_without_mojibake(tmp_path):
    base = tmp_path / "config.yaml"
    _write_yaml(base, {"app_name": "Auditor"})
    save_config({"app_name": "Já não é preciso conversão nenhuma"}, base)

    assert load_config(base)["app_name"] == "Já não é preciso conversão nenhuma"
