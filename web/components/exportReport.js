import { showToast } from "./utils.js";

function downloadBlob(content, filename, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function exportHtmlReport(dashboardRoot, audit) {
  const clone = dashboardRoot.cloneNode(true);
  clone.querySelectorAll(".internal-only").forEach((node) => node.remove());
  clone.querySelectorAll(".header-actions, .view-toggle").forEach((node) => node.remove());

  let css = "";
  try {
    css = await fetch("app.css").then((r) => r.text());
  } catch {
    /* offline export still works, just without styling */
  }

  const html = `<!doctype html>
<html lang="pt-PT">
<head>
<meta charset="utf-8" />
<title>Auditoria — ${audit.meta.domain}</title>
<style>${css}
.side-nav, .drawer-overlay, .toast-root { display: none !important; }
.dashboard-body { display: block; }
</style>
</head>
<body>
<div class="wrap" style="padding:40px 20px">${clone.outerHTML}</div>
</body>
</html>`;

  downloadBlob(html, `auditoria-${audit.meta.domain}.html`, "text/html");
  showToast("Relatório exportado.");
}

export function exportJson(audit) {
  downloadBlob(JSON.stringify(audit, null, 2), `auditoria-${audit.meta.domain}.json`, "application/json");
  showToast("JSON exportado.");
}

export async function copySummary(audit) {
  const lines = [
    `Auditoria de ${audit.meta.domain}`,
    `Pontuação de comunicação: ${audit.meta.communication_score}/100`,
    `Tracking: ${audit.meta.tracking_platforms_detected} de ${audit.meta.tracking_platforms_total} plataformas detetadas`,
    "",
    "Pontos fortes:",
    ...audit.summary.strengths.map((s) => `- ${s}`),
    "",
    "Pontos fracos:",
    ...audit.summary.weaknesses.map((s) => `- ${s}`),
    "",
    "Maiores oportunidades:",
    ...audit.summary.top_opportunities.map((s) => `- ${s}`),
  ];
  try {
    await navigator.clipboard.writeText(lines.join("\n"));
    showToast("Resumo copiado.");
  } catch {
    showToast("Não foi possível copiar (permissão do browser).");
  }
}
