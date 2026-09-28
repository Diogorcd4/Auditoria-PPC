import { registerRoute, startRouter } from "./components/router.js";
import { renderHero } from "./components/hero.js";
import { renderDashboard, renderDashboardSkeleton } from "./components/dashboard.js";
import { renderErrorCard } from "./components/placeholder.js";
import { runLiveAudit } from "./components/liveAudit.js";
import { renderHistory } from "./components/history.js";
import { renderSettings } from "./components/settings.js";
import { renderPromptsEditor } from "./components/prompts.js";
import { fetchDemoAudit, fetchSavedAudit } from "./components/state.js";

const app = document.getElementById("app");

registerRoute("/", () => renderHero(app));

registerRoute("/demo", async () => {
  renderDashboardSkeleton(app);
  try {
    const audit = await fetchDemoAudit();
    renderDashboard(app, audit, { isDemo: true });
  } catch (err) {
    renderErrorCard(app, err.message || "Não foi possível carregar a auditoria de demonstração.", () => {
      window.dispatchEvent(new Event("hashchange"));
    });
  }
});

registerRoute("/audit/:id", ({ id }) => {
  if (id === "demo") {
    window.location.hash = "#/demo";
    return;
  }
  runLiveAudit(app, id);
});

registerRoute("/report/:domain/:auditId", async ({ domain, auditId }) => {
  renderDashboardSkeleton(app);
  try {
    const audit = await fetchSavedAudit(domain, auditId);
    renderDashboard(app, audit, { isDemo: false });
  } catch (err) {
    renderErrorCard(app, err.message || "Não foi possível carregar esta auditoria.", () => {
      window.dispatchEvent(new Event("hashchange"));
    });
  }
});

registerRoute("/history", () => renderHistory(app));
registerRoute("/settings", () => renderSettings(app));
registerRoute("/prompts", () => renderPromptsEditor(app));

startRouter();
