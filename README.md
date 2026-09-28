# Auditor

Ferramenta local de auditoria de websites: cruza o que o site diz com o tracking que
tem instalado, e prepara anúncios de Google Ads e Meta Ads a partir da auditoria. Corre
100% no computador do utilizador, com IA gratuita (Ollama local por defeito) e sem custos
de API.

Este README fica completo na fase final do projeto. Por agora, para desenvolvimento:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

## Estado

Em construção por fases (ver o histórico de commits). A Fase 1 estabelece o esqueleto do
projeto, o `config.yaml`, os 7 templates de anúncios em `prompts/`, a camada de LLM
(`auditor/llm/`, com um `MockLLMClient` usado em todos os testes) e uma auditoria de
demonstração completa em `tests/fixtures/demo_audit.json`.
