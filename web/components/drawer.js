import { el, escapeHtml } from "./utils.js";

const LEVEL_LABEL = { forte: "Forte", fraco: "Fraco", ausente: "Ausente", nao_aplicavel: "Não aplicável" };
const CATEGORY_LABEL = {
  problemas: "Problemas",
  caracteristicas: "Características",
  motivos_para_comprar: "Motivos para comprar",
  objeccoes_e_receios: "Objecções e receios",
  desejos: "Desejos",
  crencas_e_mentalidade: "Crenças e mentalidade",
  oportunidades: "Oportunidades",
};

export function openPageDrawer(page) {
  const root = document.getElementById("drawer-root");
  root.innerHTML = "";

  const close = () => { root.innerHTML = ""; document.removeEventListener("keydown", onKey); };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);

  const overlay = el("div", { class: "drawer-overlay", onclick: (e) => { if (e.target === overlay) close(); } });
  const panel = el("div", { class: "drawer-panel", role: "dialog", "aria-label": `Detalhe da página ${page.pageMeta?.url ?? ""}` });

  const head = el("div", { class: "drawer-panel__head" }, [
    el("div", {}, [
      el("div", { style: "font-weight:700;font-size:1.05rem" }, page.pageMeta?.title ?? page.page_id),
      el("div", { style: "color:var(--muted);font-size:0.8rem;margin-top:4px" }, page.pageMeta?.url ?? ""),
    ]),
    el("button", { class: "drawer-close", "aria-label": "Fechar", onclick: close }, "✕"),
  ]);
  panel.appendChild(head);

  for (const key of Object.keys(CATEGORY_LABEL)) {
    const entry = page.categories[key];
    if (!entry) continue;
    const block = el("div", { class: "drawer-cat" }, [
      el("div", { class: "drawer-cat__label" }, [
        el("span", { class: `level-dot level-dot--${entry.level}` }),
        CATEGORY_LABEL[key],
        el("span", { class: "badge badge--neutral", style: "margin-left:auto" }, LEVEL_LABEL[entry.level] ?? entry.level),
      ]),
      el("div", { class: "drawer-cat__evidence" }, entry.evidence),
      entry.recommendation ? el("div", { class: "drawer-cat__rec" }, [el("strong", {}, "Recomendação"), entry.recommendation]) : null,
    ]);
    panel.appendChild(block);
  }

  if (page.cta_analysis) {
    const cta = page.cta_analysis;
    panel.appendChild(
      el("div", { class: "drawer-cat" }, [
        el("div", { class: "drawer-cat__label" }, "Análise de CTAs"),
        el("div", { class: "drawer-cat__evidence" }, `Clareza: ${cta.clarity} · Posição: ${cta.position}`),
        el("div", { class: "drawer-cat__rec" }, cta.note),
      ])
    );
  }

  overlay.appendChild(panel);
  root.appendChild(overlay);
  panel.focus?.();
}
