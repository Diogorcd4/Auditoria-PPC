import { el } from "../utils.js";

const PRIORITY_TONE = { Alta: "bad", Média: "warn", Baixa: "neutral" };

export function renderOpportunities(audit) {
  const section = el("section", { class: "section-block internal-only", id: "sec-oportunidades" });
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Oportunidades de venda"),
      el("h2", { class: "section-title" }, "O que vender, e porquê"),
      el("p", { class: "section-summary" }, "Visível só na vista interna — cada oportunidade cita a evidência que a justifica."),
    ])
  );

  const grid = el("div", { class: "grid-3" });
  for (const opp of audit.opportunities) {
    grid.appendChild(
      el("div", { class: "card opp-card" }, [
        el("div", { class: "opp-card__head" }, [
          el("span", { class: "opp-card__service" }, opp.service),
          el("span", { class: `badge badge--${PRIORITY_TONE[opp.priority] ?? "neutral"}` }, opp.priority),
        ]),
        el("div", { class: "opp-card__evidence" }, opp.evidence),
        el("div", { class: "opp-card__pitch" }, opp.pitch),
      ])
    );
  }
  section.appendChild(grid);
  return section;
}
