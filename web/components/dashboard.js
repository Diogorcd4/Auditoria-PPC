import { el, formatDate } from "./utils.js";
import { getViewMode, setViewMode } from "./state.js";
import { renderProgressSteps, renderStats } from "./progress.js";
import { renderResumo } from "./sections/resumo.js";
import { renderTracking } from "./sections/tracking.js";
import { renderCommunication } from "./sections/communication.js";
import { renderBusiness } from "./sections/business.js";
import { renderKeywords } from "./sections/keywords.js";
import { renderOpportunities } from "./sections/opportunities.js";
import { renderAds } from "./sections/ads.js";
import { renderProfile } from "./sections/profile.js";
import { exportHtmlReport, exportJson, copySummary } from "./exportReport.js";
import { showToast } from "./utils.js";

const NAV_ITEMS = [
  { id: "sec-resumo", label: "Resumo" },
  { id: "sec-tracking", label: "Tracking" },
  { id: "sec-comunicacao", label: "Comunicação" },
  { id: "sec-negocio", label: "Negócio" },
  { id: "sec-termos", label: "Termos de pesquisa" },
  { id: "sec-oportunidades", label: "Oportunidades", internal: true },
  { id: "sec-anuncios", label: "Anúncios" },
  { id: "sec-perfil", label: "Perfil e prompts", internal: true },
];

function renderSkeleton() {
  const wrap = el("div", { class: "wrap", style: "padding:60px 0" });
  for (let i = 0; i < 4; i++) {
    wrap.appendChild(el("div", { class: "skeleton skeleton-line", style: `width:${90 - i * 12}%` }));
  }
  return wrap;
}

function applyViewMode(root, mode) {
  root.querySelectorAll(".internal-only").forEach((node) => {
    node.style.display = mode === "cliente" ? "none" : "";
  });
  root.querySelectorAll(".side-nav a[data-internal='true']").forEach((link) => {
    link.style.display = mode === "cliente" ? "none" : "";
  });
}

export function renderDashboard(root, audit, { isDemo = false } = {}) {
  root.innerHTML = "";
  let viewMode = getViewMode();

  const header = el("div", { class: "wrap dashboard-header" });
  const titleRow = el("div", { class: "dashboard-title-row" }, [
    el("div", {}, [
      el("h1", {}, [audit.meta.domain, isDemo ? el("span", { class: "demo-badge" }, "DEMO") : null]),
      el("div", { class: "domain-badge" }, `Auditado em ${formatDate(audit.meta.audited_at)}`),
    ]),
  ]);

  const viewToggle = el("div", { class: "view-toggle" }, [
    el("button", { class: viewMode === "interna" ? "is-active" : "" }, "Vista interna"),
    el("button", { class: viewMode === "cliente" ? "is-active" : "" }, "Vista cliente"),
  ]);
  const [internalBtn, clientBtn] = viewToggle.children;

  const actions = el("div", { class: "header-actions" }, [
    viewToggle,
    el("button", { class: "btn btn--ghost btn--sm" }, "Exportar relatório"),
    el("button", { class: "btn btn--ghost btn--sm" }, "Exportar JSON"),
    el("button", { class: "btn btn--ghost btn--sm" }, "Copiar resumo"),
    el("button", { class: "btn btn--ghost btn--sm" }, "Imprimir"),
  ]);
  const [, exportHtmlBtn, exportJsonBtn, copyBtn, printBtn] = actions.children;

  titleRow.appendChild(actions);
  header.append(titleRow, renderProgressSteps(audit));

  const statsWrap = el("div", { class: "wrap", style: "margin-top:24px" }, renderStats(audit));

  const body = el("div", { class: "wrap dashboard-body" });
  const sideNav = el("nav", { class: "side-nav", "aria-label": "Secções da auditoria" });
  const sectionsWrap = el("div", { class: "sections" });

  const sectionRenderers = {
    "sec-resumo": renderResumo,
    "sec-tracking": renderTracking,
    "sec-comunicacao": renderCommunication,
    "sec-negocio": renderBusiness,
    "sec-termos": renderKeywords,
    "sec-oportunidades": renderOpportunities,
    "sec-anuncios": renderAds,
    "sec-perfil": renderProfile,
  };

  const ctx = {
    isDemo,
    onRegenerateAds: async (updatedProfile) => {
      if (isDemo) {
        showToast("Não disponível na demonstração — experimente numa auditoria real.");
        return;
      }
      showToast("A regenerar anúncios…");
      try {
        const res = await fetch("/api/audit/regenerate-ads", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile: updatedProfile, pages: audit.pages }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail || "Falha ao regenerar anúncios.");
        const data = await res.json();
        audit.profile = updatedProfile;
        audit.ads = data.ads;
        audit.meta.ads_valid_count = data.ads_valid_count;
        audit.meta.ads_total_count = data.ads_total_count;
        showToast("Anúncios regenerados.");
        renderDashboard(root, audit, { isDemo });
      } catch (err) {
        showToast(err.message || "Não foi possível regenerar os anúncios.");
      }
    },
  };

  for (const item of NAV_ITEMS) {
    const link = el("a", { href: `#nav-${item.id}`, "data-internal": item.internal ? "true" : "false" }, [el("span", { class: "dot is-ready" }), item.label]);
    link.addEventListener("click", (event) => {
      event.preventDefault();
      document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    sideNav.appendChild(link);

    const sectionEl = sectionRenderers[item.id](audit, ctx);
    sectionEl.classList.add("fade-in");
    sectionsWrap.appendChild(sectionEl);
  }

  body.append(sideNav, sectionsWrap);
  root.append(header, statsWrap, body);

  applyViewMode(root, viewMode);

  internalBtn.addEventListener("click", () => {
    viewMode = "interna";
    setViewMode(viewMode);
    internalBtn.classList.add("is-active");
    clientBtn.classList.remove("is-active");
    applyViewMode(root, viewMode);
  });
  clientBtn.addEventListener("click", () => {
    viewMode = "cliente";
    setViewMode(viewMode);
    clientBtn.classList.add("is-active");
    internalBtn.classList.remove("is-active");
    applyViewMode(root, viewMode);
  });

  exportHtmlBtn.addEventListener("click", () => exportHtmlReport(root, audit));
  exportJsonBtn.addEventListener("click", () => exportJson(audit));
  copyBtn.addEventListener("click", () => copySummary(audit));
  printBtn.addEventListener("click", () => window.print());

  // Highlight the side-nav item whose section is currently in view.
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        sideNav.querySelectorAll("a").forEach((a) => a.classList.remove("is-active"));
        const link = sideNav.querySelector(`a[href="#nav-${entry.target.id}"]`);
        link?.classList.add("is-active");
      }
    },
    { rootMargin: "-30% 0px -60% 0px" }
  );
  sectionsWrap.querySelectorAll(".section-block").forEach((s) => observer.observe(s));
}

export function renderDashboardSkeleton(root) {
  root.innerHTML = "";
  root.appendChild(renderSkeleton());
}
