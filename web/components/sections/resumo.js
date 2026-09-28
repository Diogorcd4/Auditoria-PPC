import { el } from "../utils.js";

export function renderResumo(audit) {
  const section = el("section", { class: "section-block", id: "sec-resumo" });
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Resumo"),
      el("h2", { class: "section-title" }, "O essencial desta auditoria"),
      el("p", { class: "section-summary" }, `Pontuação de comunicação de ${audit.meta.communication_score}/100, com ${audit.meta.opportunities_count} oportunidades de venda identificadas.`),
    ])
  );

  const grid = el("div", { class: "grid-3" });
  const columns = [
    { title: "Pontos fortes", items: audit.summary.strengths, tone: "ok" },
    { title: "Pontos fracos", items: audit.summary.weaknesses, tone: "bad" },
    { title: "Maiores oportunidades", items: audit.summary.top_opportunities, tone: "info" },
  ];
  for (const col of columns) {
    grid.appendChild(
      el("div", { class: "card" }, [
        el("h3", { style: "margin-bottom:12px;font-size:1rem" }, [el("span", { class: `badge badge--${col.tone}`, style: "margin-right:8px" }, "●"), col.title]),
        el(
          "ul",
          { style: "display:flex;flex-direction:column;gap:10px" },
          col.items.map((text) => el("li", { style: "font-size:0.86rem;color:var(--text)" }, text))
        ),
      ])
    );
  }
  section.appendChild(grid);
  return section;
}
