import { el, showToast } from "./utils.js";
import { renderDashboard } from "./dashboard.js";
import { renderErrorCard } from "./placeholder.js";

const STEP_LABELS = {
  crawl: "Site",
  tracking: "Tracking",
  comunicacao: "Comunicação",
  sintese: "Síntese",
  termos: "Termos",
  perfil: "Perfil",
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

export function runLiveAudit(root, url, { mock = false, retriesLeft = 1 } = {}) {
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

  const source = new EventSource(`/api/audit/stream?url=${encodeURIComponent(url)}${mock ? "&mock=true" : ""}`);
  let closed = false;

  const close = () => {
    if (!closed) {
      closed = true;
      source.close();
    }
  };

  cancelBtn.addEventListener("click", () => {
    close();
    showToast("Auditoria cancelada.");
    logWrap.appendChild(el("div", { class: "card" }, "Auditoria cancelada pelo utilizador."));
    cancelBtn.disabled = true;
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
      logWrap.appendChild(el("div", { class: "card", id: logId }, `A processar: ${label}…`));
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
        runLiveAudit(root, url, { mock });
      });
      const errorCard = el("div", { class: "error-card", id: logId }, [el("span", { class: "error-card__msg" }, message), retryBtn]);
      if (existing) existing.replaceWith(errorCard);
      else logWrap.appendChild(errorCard);
    }
  };

  source.onerror = () => {
    if (closed) return;
    close();
    if (retriesLeft > 0) {
      logWrap.appendChild(el("div", { class: "card" }, "Ligação ao servidor perdida — a tentar reconectar…"));
      setTimeout(() => runLiveAudit(root, url, { mock, retriesLeft: retriesLeft - 1 }), 1500);
      return;
    }
    renderErrorCard(root, "A ligação ao servidor foi perdida durante a auditoria.", () => runLiveAudit(root, url, { mock }));
  };
}
