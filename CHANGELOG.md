# Changelog

Registo das correções e mudanças de comportamento feitas ao Auditor a partir de testes reais.
Segue livremente o formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

## [Não lançado]

### Corrigido

- **Termos-semente de outro setor** (ex.: "agência de marketing digital" num site de
  transportes e logística). As sementes passam a derivar do Perfil já extraído
  (sector/produto ou serviço) - que agora corre antes dos Termos no pipeline - com fallback
  para títulos/H1 de páginas de serviço/produto, e cada semente é validada contra o
  vocabulário do próprio site antes de ser aceite.
- **Auditoria dependente da ligação do browser**: passa a correr num job em segundo plano no
  servidor (`POST /api/audits`), independente de qualquer ligação HTTP. Fechar o separador,
  recarregar a página ou o `EventSource` reconectar já não cancela nem reinicia a auditoria -
  só o botão "Cancelar" o faz. A reconexão retoma exactamente a partir do último evento
  recebido (via `Last-Event-ID`), nunca repetindo nem perdendo nada.
- **Inconsistência no relatório de tracking**: uma tag com o estado "No código, sem disparo
  observado" já não podia gerar a oportunidade "Nenhuma tag detetada" - `detected` passa a
  ser sempre `state != "Não detetado"`. Sem nenhum banner de cookies encontrado, deixa de se
  sugerir risco de RGPD só por tags dispararem "antes do consentimento".
- **Páginas irrelevantes na análise de Comunicação**: páginas legais, notícias/artigos
  datados e arquivos de blog/newsletter deixam de entrar na Comunicação e na pontuação;
  passam a ser filtradas também as versões noutra língua (`/en/`, `/fr/`, `/de/`...) quando
  existe o equivalente na língua principal do site.
- **Anúncios com promessas sem base**: superlativos e promessas ("líder", "melhor", "número
  1", "garantido", "em tempo real") só passam a validação se constarem literalmente numa das
  "ofertas verificadas" extraídas do site; caso contrário o asset fica marcado como "não
  verificado no site". Sitelinks passam a incluir o URL de destino, validado contra as
  páginas realmente rastreadas.
- Lint PT-PT deixa de marcar "seu"/"sua" (eram falsos positivos constantes em PT-PT correcto).
- O top 10 de melhorias volta a pedir ao modelo o que falta até completar 10, em vez de
  aceitar silenciosamente uma lista mais curta.
- `python -m auditor audit <url> --fast` (e o backend Gemini/`openai_compatible` em geral):
  o modelo por tarefa chegava vazio ao cliente, causando 400 "model is not specified" em
  todos os pedidos; um modelo vazio falha agora logo, em Python, sem chamar a API. 400/401/
  403/404 nunca mais são repetidos nem consomem a quota por minuto.

### Adicionado

- `config.yaml`: `crawl.languages` (línguas principais do site, por defeito `["pt"]`) e
  `crawl.max_analyzed_pages` (por defeito 12, sem `--fast`).
- `config.local.yaml` (fora do Git) guarda as definições do utilizador por cima dos valores
  por defeito de `config.yaml`, para sobreviver a trocar o zip/fazer `git pull`.
- Separador "Ofertas verificadas" no Perfil.
- Preset "Usar Gemini gratuito" nas Definições.

## Fases 1-7 (construção inicial)

Esqueleto e configuração; interface alimentada por uma fixture de demonstração; crawler e
detector de tracking; validadores de anúncios; análise de comunicação, síntese, termos e
perfil ligados a um LLM local via SSE; geração de anúncios em lotes com rondas de correcção;
histórico, definições, editor de prompts e exportações. Ver o histórico de commits para o
detalhe de cada fase.
