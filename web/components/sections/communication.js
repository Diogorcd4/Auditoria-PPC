import { el, shortPageLabel } from "../utils.js";
import { openPageDrawer } from "../drawer.js";

const CATEGORY_KEYS = ["problemas", "caracteristicas", "motivos_para_comprar", "objeccoes_e_receios", "desejos", "crencas_e_mentalidade", "oportunidades"];
const CATEGORY_SHORT = { problemas: "Problemas", caracteristicas: "Caract.", motivos_para_comprar: "Motivos", objeccoes_e_receios: "Objecções", desejos: "Desejos", crencas_e_mentalidade: "Crenças", oportunidades: "Oport." };

function pageTypeLabel(type) {
  const labels = { home: "Home", servico: "Serviço", produto: "Produto", categoria: "Categoria", precos: "Preços", sobre: "Sobre", contacto: "Contacto", faq: "FAQ", blog: "Blog", landing: "Landing", outro: "Outro" };
  return labels[type] ?? type;
}

export function renderCommunication(audit) {
  const pagesById = new Map(audit.pages.map((p) => [p.id, p]));
  const section = el("section", { class: "section-block", id: "sec-comunicacao" });
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Comunicação"),
      el("h2", { class: "section-title" }, "O que cada página diz (e não diz)"),
      el("p", { class: "section-summary" }, "Clique numa linha para ver a evidência e a recomendação por categoria."),
    ])
  );

  const pageTypes = ["todas", ...new Set(audit.pages.map((p) => p.type))];
  const filters = el("div", { class: "matrix-filters" });
  let activeType = "todas";

  const scrollWrap = el("div", { class: "matrix-scroll" });
  section.append(filters, scrollWrap);

  function renderTable() {
    scrollWrap.innerHTML = "";
    const table = el("table", { class: "matrix" });
    const thead = el("tr", {}, [el("th", {}, "Página"), ...CATEGORY_KEYS.map((k) => el("th", { class: "level-dot-cell" }, CATEGORY_SHORT[k]))]);
    table.appendChild(el("thead", {}, thead));

    const tbody = el("tbody");
    const rows = audit.communication.pages.filter((row) => activeType === "todas" || pagesById.get(row.page_id)?.type === activeType);
    for (const row of rows) {
      const page = pagesById.get(row.page_id);
      const tr = el("tr", {
        tabindex: "0",
        onclick: () => openPageDrawer({ ...row, pageMeta: page }),
        onkeydown: (e) => { if (e.key === "Enter") openPageDrawer({ ...row, pageMeta: page }); },
      });
      tr.appendChild(
        el("td", {}, el("div", { class: "page-cell" }, [el("strong", {}, shortPageLabel(page) || row.page_id), el("span", {}, pageTypeLabel(page?.type))]))
      );
      for (const key of CATEGORY_KEYS) {
        const level = row.categories[key]?.level ?? "nao_aplicavel";
        tr.appendChild(el("td", { class: "level-dot-cell" }, el("span", { class: `level-dot level-dot--${level}`, title: level })));
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    scrollWrap.appendChild(table);
  }

  for (const type of pageTypes) {
    const pill = el("button", { class: `filter-pill${type === activeType ? " is-active" : ""}` }, type === "todas" ? "Todas" : pageTypeLabel(type));
    pill.addEventListener("click", () => {
      activeType = type;
      filters.querySelectorAll(".filter-pill").forEach((p) => p.classList.remove("is-active"));
      pill.classList.add("is-active");
      renderTable();
    });
    filters.appendChild(pill);
  }
  renderTable();

  const improvementsCard = el("div", { class: "card", style: "margin-top:20px" }, [
    el("h3", { style: "font-size:1rem;margin-bottom:4px" }, "Top 10 melhorias"),
    el("p", { style: "color:var(--muted);font-size:0.82rem;margin-bottom:4px" }, "Priorizadas por impacto e esforço."),
  ]);
  const list = el("div", { class: "improvements-list" });
  for (const item of audit.communication.site_synthesis.top_improvements) {
    const page = pagesById.get(item.page);
    list.appendChild(
      el("div", { class: "improvement-row" }, [
        el("div", {}, [el("div", { class: "improvement-row__title" }, item.title), el("div", { class: "improvement-row__page" }, page?.url ?? item.page)]),
        el("div", { class: "improvement-row__tags" }, [
          el("span", { class: `badge badge--${item.impact === "alto" ? "bad" : item.impact === "médio" ? "warn" : "neutral"}` }, `Impacto ${item.impact}`),
          el("span", { class: "badge badge--info" }, `Esforço ${item.effort}`),
        ]),
      ])
    );
  }
  improvementsCard.appendChild(list);
  section.appendChild(improvementsCard);

  return section;
}
