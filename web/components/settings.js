import { el, showToast } from "./utils.js";
import { fetchSettings, saveSettings } from "./state.js";

const TASKS = [
  { key: "analise", label: "Análise de comunicação" },
  { key: "keywords", label: "Termos de pesquisa" },
  { key: "perfil", label: "Perfil" },
  { key: "anuncios", label: "Anúncios" },
];

const GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/";

async function testOllama(ollamaUrl) {
  const res = await fetch(`/api/settings/test-ollama?ollama_url=${encodeURIComponent(ollamaUrl)}`);
  if (!res.ok) return { reachable: false, models: [] };
  return res.json();
}

async function testOpenAICompatible(baseUrl) {
  const res = await fetch(`/api/settings/test-openai-compatible?base_url=${encodeURIComponent(baseUrl)}`);
  if (!res.ok) return { reachable: false, models: [], suggested_model: null, detail: "Falha ao contactar o servidor." };
  return res.json();
}

export async function renderSettings(root) {
  root.innerHTML = "";
  const wrap = el("div", { class: "wrap", style: "padding:48px 0;max-width:760px" });
  wrap.append(
    el("div", { class: "eyebrow" }, "Definições"),
    el("h1", { class: "section-title", style: "margin-bottom:4px" }, "Motor de IA, limites e marca"),
    el("p", { class: "section-summary", style: "margin-bottom:24px" }, "Tudo aqui fica gravado em config.local.yaml, no seu computador (nunca se perde ao actualizar o projecto).")
  );
  root.appendChild(wrap);

  let settings;
  try {
    settings = await fetchSettings();
  } catch {
    wrap.appendChild(el("div", { class: "error-card" }, el("span", { class: "error-card__msg" }, "Não foi possível carregar as definições.")));
    return;
  }

  // --- Motor de IA ---
  const engineCard = el("div", { class: "card" }, [el("h3", { style: "margin-bottom:14px" }, "Motor de IA")]);
  const backendSelect = el(
    "select",
    { style: "width:100%;background:var(--bg-2);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px;margin-bottom:14px" },
    [
      el("option", { value: "ollama", selected: settings.llm.backend !== "openai_compatible" ? "selected" : undefined }, "Ollama (local)"),
      el("option", { value: "openai_compatible", selected: settings.llm.backend === "openai_compatible" ? "selected" : undefined }, "Compatível com OpenAI (ex.: Gemini gratuito)"),
    ]
  );
  const geminiPresetBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Usar Gemini gratuito");

  const statusRow = el("div", { class: "engine-status", style: "margin-bottom:16px" }, [el("span", { class: "dot" }), el("span", {}, "A verificar…")]);
  const urlInput = el("input", { value: settings.llm.ollama_url, style: "width:100%;margin-bottom:12px" });
  urlInput.className = "";
  const testBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Testar ligação");
  const numCtxInput = el("input", { type: "number", value: settings.llm.num_ctx, style: "width:140px" });
  const ollamaFieldsWrap = el("div", {}, [
    statusRow,
    el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px" }, "URL do Ollama"),
    el("div", { style: "display:flex;gap:8px;margin-bottom:16px" }, [urlInput, testBtn]),
    el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px" }, "Contexto (num_ctx)"),
    numCtxInput,
  ]);

  const openaiStatusRow = el("div", { class: "engine-status", style: "margin-bottom:16px" }, [el("span", { class: "dot" }), el("span", {}, "Por testar…")]);
  const openaiBaseUrlInput = el("input", { value: settings.llm.openai_compatible?.base_url || GEMINI_BASE_URL, style: "width:100%;margin-bottom:12px" });
  const openaiTestBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Testar ligação");
  const openaiFieldsWrap = el("div", { style: "display:none" }, [
    el("p", { style: "font-size:0.8rem;color:var(--muted);margin-bottom:12px" }, "A chave de API nunca se introduz aqui: é lida do ficheiro .env no servidor (AUDITOR_API_KEY)."),
    openaiStatusRow,
    el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px" }, "URL base (compatível com OpenAI)"),
    el("div", { style: "display:flex;gap:8px;margin-bottom:16px" }, [openaiBaseUrlInput, openaiTestBtn]),
  ]);

  function syncBackendFieldsVisibility() {
    const usingOpenAI = backendSelect.value === "openai_compatible";
    ollamaFieldsWrap.style.display = usingOpenAI ? "none" : "";
    openaiFieldsWrap.style.display = usingOpenAI ? "" : "none";
  }
  backendSelect.addEventListener("change", syncBackendFieldsVisibility);

  // Quando o preset "Gemini gratuito" é usado, o backend das quatro tarefas passa a ser
  // definido explicitamente (nunca fica a "" à espera de herdar o backend global) - secção 5:
  // o botão tem de actualizar backend E modelo das quatro tarefas, não só o backend geral.
  let taskBackendOverride = null;

  async function refreshOpenAICompatibleStatus({ forceModel = false } = {}) {
    const dot = openaiStatusRow.querySelector(".dot");
    const label = openaiStatusRow.querySelector("span:last-child");
    dot.className = "dot";
    label.textContent = "A verificar…";
    const { reachable, models, suggested_model, detail } = await testOpenAICompatible(openaiBaseUrlInput.value);
    if (reachable) {
      dot.classList.add("is-ok");
      label.textContent = `Ligado — ${models.length} modelo(s) disponível(is)`;
      if (suggested_model) {
        for (const task of TASKS) {
          const select = modelSelects[task.key];
          if (forceModel || !select.value) {
            select.innerHTML = "";
            select.appendChild(el("option", { value: suggested_model, selected: "selected" }, suggested_model));
          }
        }
      }
    } else {
      dot.classList.add("is-bad");
      label.textContent = detail || "Não foi possível ligar.";
    }
    return { reachable, suggested_model };
  }
  openaiTestBtn.addEventListener("click", () => refreshOpenAICompatibleStatus());

  geminiPresetBtn.addEventListener("click", async () => {
    backendSelect.value = "openai_compatible";
    syncBackendFieldsVisibility();
    openaiBaseUrlInput.value = GEMINI_BASE_URL;
    geminiPresetBtn.disabled = true;
    geminiPresetBtn.textContent = "A testar…";
    try {
      const { reachable, suggested_model } = await refreshOpenAICompatibleStatus({ forceModel: true });
      if (reachable) {
        taskBackendOverride = "openai_compatible";
        if (!suggested_model) {
          for (const task of TASKS) {
            const select = modelSelects[task.key];
            select.innerHTML = "";
            select.appendChild(el("option", { value: "gemini-flash-lite-latest", selected: "selected" }, "gemini-flash-lite-latest"));
          }
        }
      }
      showToast(reachable ? `Gemini gratuito activado nas quatro tarefas${suggested_model ? ` — modelo: ${suggested_model}` : " — modelo: gemini-flash-lite-latest"}.` : "Não foi possível ligar ao Gemini. Verifique AUDITOR_API_KEY no .env.");
    } finally {
      geminiPresetBtn.disabled = false;
      geminiPresetBtn.textContent = "Usar Gemini gratuito";
    }
  });

  const modelSelects = {};
  const modelsWrap = el("div", { style: "display:flex;flex-direction:column;gap:12px;margin-top:14px" });
  for (const task of TASKS) {
    const currentModel = settings.llm.tasks?.[task.key]?.model || "";
    const select = el("select", { style: "width:100%;background:var(--bg-2);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px" });
    select.appendChild(el("option", { value: "" }, "— escolher automaticamente —"));
    if (currentModel) select.appendChild(el("option", { value: currentModel, selected: "selected" }, currentModel));
    modelSelects[task.key] = select;
    modelsWrap.appendChild(
      el("div", {}, [el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px" }, task.label), select])
    );
  }

  async function refreshOllamaStatus() {
    const dot = statusRow.querySelector(".dot");
    const label = statusRow.querySelector("span:last-child");
    dot.className = "dot";
    label.textContent = "A verificar…";
    const { reachable, models } = await testOllama(urlInput.value);
    if (reachable) {
      dot.classList.add("is-ok");
      label.textContent = `Ligado — ${models.length} modelo(s) instalado(s)`;
      for (const task of TASKS) {
        const select = modelSelects[task.key];
        const current = select.value;
        select.innerHTML = "";
        select.appendChild(el("option", { value: "" }, "— escolher automaticamente —"));
        for (const m of models) {
          select.appendChild(el("option", { value: m, selected: m === current ? "selected" : undefined }, m));
        }
      }
    } else {
      dot.classList.add("is-bad");
      label.innerHTML = "";
      label.append("Não foi possível ligar — ", el("a", { href: "https://ollama.com/download" }, "instalar o Ollama"));
    }
  }
  testBtn.addEventListener("click", refreshOllamaStatus);

  engineCard.append(
    el("div", { style: "display:flex;gap:8px;align-items:flex-end;margin-bottom:4px" }, [
      el("div", { style: "flex:1" }, backendSelect),
      geminiPresetBtn,
    ]),
    ollamaFieldsWrap,
    openaiFieldsWrap,
    el("h4", { style: "margin-top:20px;margin-bottom:4px;font-size:0.85rem" }, "Modelo por tarefa"),
    modelsWrap
  );
  wrap.appendChild(engineCard);
  syncBackendFieldsVisibility();
  refreshOllamaStatus();

  // --- Limites do crawl ---
  const crawlCard = el("div", { class: "card", style: "margin-top:20px" }, [el("h3", { style: "margin-bottom:14px" }, "Limites do crawl")]);
  const maxPagesInput = el("input", { type: "number", value: settings.crawl.max_pages, style: "width:100px" });
  const delayInput = el("input", { type: "number", step: "0.1", value: settings.crawl.delay_seconds, style: "width:100px" });
  const robotsInput = el("input", { type: "checkbox" });
  if (settings.crawl.respect_robots) robotsInput.checked = true;
  crawlCard.append(
    el("div", { class: "grid-2" }, [
      el("div", {}, [el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px" }, "Máximo de páginas"), maxPagesInput]),
      el("div", {}, [el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px" }, "Pausa entre páginas (s)"), delayInput]),
    ]),
    el("label", { style: "display:flex;align-items:center;gap:8px;margin-top:14px;font-size:0.85rem" }, [robotsInput, "Respeitar robots.txt"])
  );
  wrap.appendChild(crawlCard);

  // --- Marca e serviços ---
  const brandCard = el("div", { class: "card", style: "margin-top:20px" }, [el("h3", { style: "margin-bottom:14px" }, "Marca e serviços")]);
  const appNameInput = el("input", { value: settings.app_name, style: "width:100%" });
  const servicesInput = el("input", { value: settings.owner_services.join(", "), style: "width:100%" });
  brandCard.append(
    el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px" }, "Nome da marca"),
    appNameInput,
    el("label", { style: "font-size:0.8rem;color:var(--muted);display:block;margin:14px 0 4px" }, "Serviços (usados nas oportunidades de venda, separados por vírgula)"),
    servicesInput
  );
  wrap.appendChild(brandCard);

  const saveBtn = el("button", { class: "btn btn--primary", style: "margin-top:20px" }, "Guardar definições");
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "A guardar…";
    try {
      const payload = {
        app_name: appNameInput.value,
        owner_services: servicesInput.value.split(",").map((s) => s.trim()).filter(Boolean),
        llm: {
          ...settings.llm,
          backend: backendSelect.value,
          ollama_url: urlInput.value,
          num_ctx: Number(numCtxInput.value) || settings.llm.num_ctx,
          openai_compatible: { ...settings.llm.openai_compatible, base_url: openaiBaseUrlInput.value },
          tasks: Object.fromEntries(TASKS.map((t) => [t.key, { backend: taskBackendOverride ?? (settings.llm.tasks?.[t.key]?.backend || ""), model: modelSelects[t.key].value }])),
        },
        crawl: {
          max_pages: Number(maxPagesInput.value) || settings.crawl.max_pages,
          delay_seconds: Number(delayInput.value) || settings.crawl.delay_seconds,
          respect_robots: robotsInput.checked,
        },
      };
      settings = await saveSettings(payload);
      showToast("Definições guardadas.");
    } catch (err) {
      showToast(err.message || "Não foi possível guardar.");
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Guardar definições";
    }
  });
  wrap.appendChild(saveBtn);
}
