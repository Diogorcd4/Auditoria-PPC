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

export async function fetchHistory() {
  const res = await fetch("/api/history");
  if (!res.ok) return [];
  return res.json();
}

export async function fetchSavedAudit(domain, auditId) {
  const res = await fetch(`/api/audits/${encodeURIComponent(domain)}/${encodeURIComponent(auditId)}`);
  if (!res.ok) throw new Error("Não foi possível carregar esta auditoria.");
  return res.json();
}

export async function deleteSavedAudit(domain, auditId) {
  const res = await fetch(`/api/audits/${encodeURIComponent(domain)}/${encodeURIComponent(auditId)}`, { method: "DELETE" });
  return res.ok;
}

export async function fetchRecentAudits(limit = 6) {
  const history = await fetchHistory();
  return history.slice(0, limit).map((entry) => ({
    id: `${entry.domain}/${entry.audit_id}`,
    domain: entry.domain,
    audit_id: entry.audit_id,
    date: new Date(entry.date).toLocaleDateString("pt-PT"),
    score: entry.score,
  }));
}

export async function checkEngineStatus() {
  try {
    const res = await fetch("/api/settings/test-ollama");
    if (!res.ok) return { backend: "ollama", reachable: false, models: [] };
    const data = await res.json();
    return { backend: "ollama", reachable: data.reachable, models: data.models };
  } catch {
    return { backend: "ollama", reachable: false, models: [] };
  }
}

export async function fetchSettings() {
  const res = await fetch("/api/settings");
  if (!res.ok) throw new Error("Não foi possível carregar as definições.");
  return res.json();
}

export async function saveSettings(settings) {
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error("Não foi possível guardar as definições.");
  return res.json();
}

export async function fetchPrompts() {
  const res = await fetch("/api/prompts");
  if (!res.ok) throw new Error("Não foi possível carregar os prompts.");
  return res.json();
}

export async function savePrompt(name, content) {
  const res = await fetch(`/api/prompts/${encodeURIComponent(name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("Não foi possível guardar este prompt.");
  return res.json();
}

export async function resetPrompt(name) {
  const res = await fetch(`/api/prompts/${encodeURIComponent(name)}/reset`, { method: "POST" });
  if (!res.ok) throw new Error("Não foi possível repor este prompt.");
  return res.json();
}
