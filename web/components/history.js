import { el, showToast } from "./utils.js";
import { deleteSavedAudit, fetchHistory } from "./state.js";

export async function renderHistory(root) {
  root.innerHTML = "";

  const wrap = el("div", { class: "wrap", style: "padding:48px 0" });
  wrap.append(
    el("div", { class: "eyebrow" }, "Histórico"),
    el("h1", { class: "section-title", style: "margin-bottom:4px" }, "Auditorias anteriores"),
    el("p", { class: "section-summary", style: "margin-bottom:24px" }, "Todas as auditorias ficam guardadas localmente, em output/.")
  );

  const searchInput = el("input", {
    type: "text",
    placeholder: "Pesquisar por domínio…",
    class: "profile-table",
    style: "max-width:320px;padding:10px 14px;border-radius:999px;border:1px solid var(--border);background:var(--surface);color:var(--text);margin-bottom:20px;display:block",
  });
  wrap.appendChild(searchInput);

  const grid = el("div", { class: "recents-grid" });
  wrap.appendChild(grid);
  root.appendChild(wrap);

  let entries = [];
  try {
    entries = await fetchHistory();
  } catch {
    entries = [];
  }

  function renderGrid(filter) {
    grid.innerHTML = "";
    const filtered = filter ? entries.filter((e) => e.domain.toLowerCase().includes(filter.toLowerCase())) : entries;
    if (filtered.length === 0) {
      grid.appendChild(el("div", { class: "card", style: "grid-column:1/-1;color:var(--muted)" }, entries.length === 0 ? "Ainda não há auditorias guardadas. Comece uma a partir do início." : "Nenhum resultado para essa pesquisa."));
      return;
    }
    for (const entry of filtered) {
      const card = el("div", { class: "card recent-card", style: "display:flex;flex-direction:column;gap:10px" });
      card.append(
        el("div", { class: "recent-card__domain" }, entry.domain),
        el("div", { class: "recent-card__meta" }, [
          el("span", {}, new Date(entry.date).toLocaleDateString("pt-PT")),
          el("span", {}, `${entry.score}/100`),
        ]),
        el("div", { style: "display:flex;gap:8px;margin-top:6px" }, [
          el("a", { class: "btn btn--ghost btn--sm", href: `#/report/${encodeURIComponent(entry.domain)}/${encodeURIComponent(entry.audit_id)}` }, "Abrir"),
          el("button", {
            class: "btn btn--ghost btn--sm",
            onclick: async () => {
              const ok = await deleteSavedAudit(entry.domain, entry.audit_id);
              if (ok) {
                entries = entries.filter((e) => !(e.domain === entry.domain && e.audit_id === entry.audit_id));
                renderGrid(searchInput.value);
                showToast("Auditoria apagada.");
              } else {
                showToast("Não foi possível apagar esta auditoria.");
              }
            },
          }, "Apagar"),
        ])
      );
      grid.appendChild(card);
    }
  }

  searchInput.addEventListener("input", () => renderGrid(searchInput.value));
  renderGrid("");
}
