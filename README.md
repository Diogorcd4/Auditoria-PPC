# Auditor

Ferramenta local de auditoria de websites: cruza o que o site diz com o tracking que tem
instalado, avalia a comunicação de cada página em 7 categorias, sugere termos de pesquisa
e prepara anúncios de Google Ads e Meta Ads já prontos a colar nas contas.

Corre 100% no computador do utilizador. IA gratuita por omissão (Ollama local, sem chaves
de API nem custos), interface em português de Portugal, e sem qualquer telemetria.

## Instalação

Precisa de **Python 3.11 ou superior**.

```bash
git clone <o URL deste repositório>
cd Auditoria-PPC
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
playwright install chromium
```

### Instalar o Ollama (motor de IA gratuito)

1. Descarregue e instale o Ollama a partir de [ollama.com/download](https://ollama.com/download).
2. Escolha um modelo consoante a memória RAM do seu computador (confirme os nomes e tamanhos
   atuais em [ollama.com/library](https://ollama.com/library), que mudam com regularidade):
   - **16 GB de RAM**: um modelo na casa dos 4 a 12 mil milhões de parâmetros costuma ser o
     limite confortável (por exemplo, um modelo de 7-8 mil milhões).
   - **32 GB de RAM ou mais**: já consegue correr um modelo maior com folga.
3. Descarregue o modelo escolhido:

   ```bash
   ollama pull <nome-do-modelo>
   ```

O Ollama fica a correr em segundo plano (`http://localhost:11434`) — o Auditor liga-se lá
automaticamente. Não é preciso nenhuma chave de API.

## Arrancar

```bash
python -m auditor serve
```

Abre o browser em `http://127.0.0.1:8000`. Sem nenhum modelo instalado, ainda pode explorar
a interface por inteiro através da demonstração (`http://127.0.0.1:8000/#/demo`), que usa
dados fictícios e nunca contacta o Ollama nem a rede.

### Linha de comandos

```bash
python -m auditor audit <url> [opções]
```

| Opção | Efeito |
|---|---|
| `--max-pages N` | Limite de páginas a rastrear (por omissão, 25) |
| `--only crawl,tracking` | Corre só os passos indicados do pipeline (`crawl`, `tracking`, `comunicacao`, `sintese`, `termos`, `perfil`, `anuncios`, `relatorio`) |
| `--dry-run` | Mostra o que seria feito, sem executar nada |
| `--resume` | Retoma a auditoria mais recente por concluir para este domínio, sem repetir os passos já feitos |
| `--mock` | Usa dados fictícios em vez do Ollama (útil para testar sem esperar por um modelo local) |

## O que a ferramenta faz

- **Estrutura do site**: rastreia até ao limite configurado, classifica cada página (home,
  serviço, produto, preços, sobre, contacto, FAQ, blog, landing...) e extrai o conteúdo
  relevante de cada uma.
- **Tracking instalado**: deteta Google Analytics 4, Universal Analytics, Google Tag
  Manager, Google Ads, Meta Pixel, Microsoft Advertising (UET), Consent Mode v2 e a CMP de
  cookies usada — sempre com evidência, nunca a adivinhar. Os pedidos de conversão são
  sempre abortados antes de saírem do browser, para nunca poluir as contas reais do site
  auditado.
- **Comunicação por página**: compara cada página com 7 categorias (Problemas,
  Características, Motivos para comprar, Objecções e receios, Desejos, Crenças e
  mentalidade, Oportunidades), com evidência e recomendação por categoria.
- **Termos de pesquisa**: observados (Google Autocomplete) e inferidos (agrupados por IA em
  etapas de intenção), com lacunas de conteúdo e negativas sugeridas.
- **Anúncios**: os 7 templates de Google Search, Performance Max e Meta Ads, preenchidos
  automaticamente a partir do perfil extraído do site, gerados em lotes e validados contra
  os limites de caracteres de cada plataforma, com rondas de correção automáticas.

Tudo fica guardado em `output/<domínio>/<auditoria>/`, incluindo os prompts efetivamente
enviados ao modelo (`prompts_preenchidos/`) e o relatório final (`audit.json`).

## Desenvolvimento

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Toda a suite de testes corre sem rede e sem Ollama: a camada de IA é sempre substituída por
um `MockLLMClient` determinístico, e o tracking/crawler são testados contra páginas HTML
sintéticas servidas localmente.

## Resolução de problemas

**"Ollama não encontrado" na interface ou nas Definições**
Confirme que o Ollama está instalado e a correr (`ollama list` no terminal deve funcionar).
Se o instalou depois de já ter aberto o Auditor, clique em "Testar ligação" nas Definições.

**O Chromium não arranca / `playwright install` falha**
Corra `playwright install chromium` novamente. Se a rede da sua empresa bloquear downloads,
poderá ser preciso configurar um proxy nas variáveis de ambiente `HTTPS_PROXY`/`HTTP_PROXY`
antes de instalar.

**Uma auditoria está muito lenta**
Modelos locais pequenos demoram mais do que uma API paga. Considere reduzir `--max-pages`,
usar um modelo mais pequeno nas Definições, ou correr a auditoria com `--only` para testar
só os passos que lhe interessam de cada vez.

**Uma auditoria foi interrompida a meio**
Volte a abrir a mesma URL — a interface liga-se de novo ao mesmo progresso, sem repetir os
passos já concluídos. Na linha de comandos, use `--resume`.

**Quero recomeçar uma auditoria do zero**
Apague a pasta correspondente em `output/<domínio>/` (ou a pasta `output/` inteira, para
limpar tudo) e a próxima auditoria começa outra vez do início.

## Privacidade

Nada é enviado para fora do seu computador além do necessário para visitar o próprio site
que está a auditar (e, se escolher um backend de IA que não seja o Ollama local, para esse
serviço). Sem telemetria, sem conta, sem chaves de API por omissão.
