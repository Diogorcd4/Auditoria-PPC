import { el, shortPageLabel } from "../utils.js";

const CATEGORY_LABEL = {
  problemas: "Problemas",
  caracteristicas: "Características",
  motivos_para_comprar: "Motivos para comprar",
  objeccoes_e_receios: "Objecções e receios",
  desejos: "Desejos",
  crencas_e_mentalidade: "Crenças e mentalidade",
  oportunidades: "Oportunidades",
};

export function renderBusiness(audit) {
  const pagesById = new Map(audit.pages.map((p) => [p.id, p]));
  const section = el("section", { class: "section-block", id: "sec-negocio" });
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Negócio em 7 categorias"),
      el("h2", { class: "section-title" }, "O mapa de insights do site"),
      el("p", { class: "section-summary" }, "Só o que o site suporta — cada ponto indica a página de origem."),
    ])
  );

  const grid = el("div", { class: "grid-3" });
  for (const [key, label] of Object.entries(CATEGORY_LABEL)) {
    const items = audit.business_categories[key] ?? [];
    const card = el("div", { class: "card biz-card" }, [el("h3", {}, label)]);
    if (items.length === 0) {
      card.appendChild(el("div", { class: "empty" }, "Não consta no site."));
    } else {
      card.appendChild(
        el(
          "ul",
          {},
          items.map((item) => {
            const page = pagesById.get(item.source_page);
            return el("li", {}, [item.text, el("span", { class: "src" }, shortPageLabel(page) || item.source_page)]);
          })
        )
      );
    }
    grid.appendChild(card);
  }
  section.appendChild(grid);
  return section;
}
