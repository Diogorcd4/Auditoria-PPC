import { el, showToast } from "./utils.js";
import { renderDashboard } from "./dashboard.js";
import { renderErrorCard } from "./placeholder.js";

const STEP_LABELS = {
  crawl: "Site",
  tracking: "Tracking",
  comunicacao: "Comunicação",
  sintese: "Síntese",
  perfil: "Perfil",
  termos: "Termos",
  anuncios: "Anúncios",
  relatorio: "Relatório",
};
const STEP_ORDER = Object.keys(STEP_LABELS);

function statusClass(status) {
  if (status === "done") return "is-done";
  if (status === "running") return "is-active";
  if (status === "error") return "is-error";
  return "";
}

function renderStepsBar(stepStates) {
  return el(
    "div",
    { class: "progress-steps" },
    STEP_ORDER.map((key) => el("div", { class: `progress-step ${statusClass(stepStates[key])}`.trim() }, [el("span", { class: "dot" }), STEP_LABELS[key]]))
  );
}

async function startOrReuseJob(url, { mock, fast }) {
  const res = await fetch("/api/audits", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, mock, fast }),
  });
  if (!res.ok) throw new Error("Não foi possível iniciar a auditoria.");
  return res.json(); // {id, reused}
}

/**
 * Auditoria em tempo real, ligada a um job em segundo plano no servidor (secção B do pedido
 * de correcção): fechar este separador, recarregar a página ou uma ligação SSE cair e
 * voltar a ligar-se nunca cancela nem reinicia o job - só o botão "Cancelar" o faz. Abrir de
 * novo #/audit/<url> (job em curso ou já terminado) reconstrói tudo a partir da reprodução de
 * eventos que a ligação SSE já faz sozinha, desde o início do job.
 */
export async function runLiveAudit(root, url, { mock = false, fast = false } = {}) {
  root.innerHTML = "";
  const stepStates = {};

  const header = el("div", { class: "wrap dashboard-header" });
  const cancelBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Cancelar");
  header.append(
    el("div", { class: "dashboard-title-row" }, [
      el("div", {}, [el("h1", {}, url), el("div", { class: "domain-badge" }, "A auditar em tempo real…")]),
      el("div", { class: "header-actions" }, [cancelBtn]),
    ])
  );
  const stepsBarWrap = el("div", {}, renderStepsBar(stepStates));
  header.appendChild(stepsBarWrap);

  const logWrap = el("div", { class: "wrap", style: "margin-top:24px;display:flex;flex-direction:column;gap:10px" });
  root.append(header, logWrap);

  let jobId;
  try {
    const { id, reused } = await startOrReuseJob(url, { mock, fast });
    jobId = id;
    if (reused) {
      logWrap.appendChild(el("div", { class: "card" }, "A ligar a uma auditoria já em curso para este site…"));
    }
  } catch (err) {
    renderErrorCard(root, err.message || "Não foi possível iniciar a auditoria.", () => runLiveAudit(root, url, { mock, fast }));
    return;
  }

  let closed = false;
  const source = new EventSource(`/api/audits/${encodeURIComponent(jobId)}/events`);

  const close = () => {
    if (!closed) {
      closed = true;
      source.close();
    }
  };

  cancelBtn.addEventListener("click", async () => {
    close();
    cancelBtn.disabled = true;
    try {
      await fetch(`/api/audits/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    } catch {
      // best-effort - a ligação local já foi fechada de qualquer forma
    }
    showToast("Auditoria cancelada.");
    logWrap.appendChild(el("div", { class: "card" }, "Auditoria cancelada pelo utilizador."));
  });

  source.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    stepStates[payload.step] = payload.status;
    stepsBarWrap.innerHTML = "";
    stepsBarWrap.appendChild(renderStepsBar(stepStates));

    const logId = `log-${payload.step}`;
    const existing = logWrap.querySelector(`#${logId}`);
    const label = STEP_LABELS[payload.step] || payload.step;

    if (payload.status === "running") {
      if (!existing) logWrap.appendChild(el("div", { class: "card", id: logId }, `A processar: ${label}…`));
    } else if (payload.status === "progress") {
      const message = payload.message ? `${label}: ${payload.message}` : `A processar: ${label}…`;
      if (existing) existing.textContent = message;
      else logWrap.appendChild(el("div", { class: "card", id: logId }, message));
    } else if (payload.status === "done") {
      const message = `${label} concluído em ${payload.duration_seconds.toFixed(1)}s.`;
      if (existing) existing.textContent = message;
      else logWrap.appendChild(el("div", { class: "card", id: logId }, message));

      if (payload.step === "relatorio") {
        close();
        renderDashboard(root, payload.data, { isDemo: false });
      }
    } else if (payload.status === "error") {
      const message = `${label}: não foi possível concluir — ${payload.error}`;
      const retryBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Tentar de novo");
      retryBtn.addEventListener("click", () => {
        close();
        // Um novo POST /api/audits para o mesmo domínio arranca um job novo que retoma a
        // partir dos checkpoints já guardados (resume=True) - só repete o passo que falhou.
        runLiveAudit(root, url, { mock, fast });
      });
      const errorCard = el("div", { class: "error-card", id: logId }, [el("span", { class: "error-card__msg" }, message), retryBtn]);
      if (existing) existing.replaceWith(errorCard);
      else logWrap.appendChild(errorCard);
    }
  };

  source.onerror = () => {
    if (closed) return;
    // O EventSource do browser tenta reconectar automaticamente, enviando Last-Event-ID -
    // nunca fechamos nem reiniciamos nada aqui, só avisamos sem perder o que já foi mostrado
    // (secção B.1: uma ligação a cair e a voltar a ligar-se nunca cancela nem reinicia o job).
    const banner = logWrap.querySelector("#connection-banner");
    const message = "Ligação ao servidor instável — a tentar reconectar automaticamente (o progresso da auditoria não se perde)…";
    if (banner) banner.textContent = message;
    else logWrap.insertBefore(el("div", { class: "card", id: "connection-banner" }, message), logWrap.firstChild);
  };

  source.addEventListener("open", () => {
    logWrap.querySelector("#connection-banner")?.remove();
  });
}
