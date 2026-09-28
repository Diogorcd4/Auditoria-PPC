const VIEW_MODE_KEY = "auditor:view-mode";

export function getViewMode() {
  try {
    return localStorage.getItem(VIEW_MODE_KEY) === "cliente" ? "cliente" : "interna";
  } catch {
    return "interna";
  }
}

export function setViewMode(mode) {
  try {
    localStorage.setItem(VIEW_MODE_KEY, mode);
  } catch {
    /* private browsing or blocked storage: view mode just won't persist across reloads */
  }
}

export async function fetchDemoAudit() {
  const res = await fetch("/api/demo");
  if (!res.ok) throw new Error("Não foi possível carregar a auditoria de demonstração.");
  return res.json();
}

export async function fetchRecentAudits() {
  // No history backend yet (Fase 7); an empty list keeps the hero screen honest about it.
  return [];
}

export async function checkEngineStatus() {
  // No LLM backend wired yet (Fase 5); reported as unknown rather than faking a state.
  return { backend: "ollama", reachable: null };
}
