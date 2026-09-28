import { registerRoute, startRouter } from "./components/router.js";
import { renderHero } from "./components/hero.js";
import { renderDashboard, renderDashboardSkeleton } from "./components/dashboard.js";
import { renderPlaceholder, renderErrorCard } from "./components/placeholder.js";
import { fetchDemoAudit } from "./components/state.js";

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
  renderPlaceholder(app, `Auditoria de ${id}`, "A execução de auditorias reais liga-se ao motor de crawling e à IA local numa fase seguinte desta construção. Por agora, veja a demonstração em #/demo.");
});

registerRoute("/history", () => renderPlaceholder(app, "Histórico", "Esta secção fica disponível numa fase seguinte da construção."));
registerRoute("/settings", () => renderPlaceholder(app, "Definições", "Esta secção fica disponível numa fase seguinte da construção."));
registerRoute("/prompts", () => renderPlaceholder(app, "Prompts", "O editor dos 7 prompts fica disponível numa fase seguinte da construção."));

startRouter();
