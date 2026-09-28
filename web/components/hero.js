import { el } from "./utils.js";
import { fetchRecentAudits, checkEngineStatus } from "./state.js";

function normalizeUrl(raw) {
  let value = raw.trim();
  if (!value) return "";
  if (!/^https?:\/\//i.test(value)) value = `https://${value}`;
  return value;
}

export async function renderHero(root) {
  root.innerHTML = "";

  const section = el("section", { class: "hero" });
  const inner = el("div", { class: "wrap hero__inner" });

  const eyebrow = el("div", { class: "eyebrow" }, "Auditoria de websites");
  const title = el("h1", {}, [
    "Cole um URL. Veja tudo o que o site ",
    el("span", { class: "gradient-word" }, "esconde."),
  ]);
  const lead = el("p", { class: "lead" }, "Tracking instalado, comunicação por página e anúncios de Google Ads e Meta Ads já preenchidos — tudo a correr no seu computador, sem custos de API.");

  const form = el("form", { class: "url-form" });
  const input = el("input", { type: "text", placeholder: "empresa.pt", "aria-label": "URL do site a auditar", autocomplete: "off" });
  const submitBtn = el("button", { type: "submit", class: "btn btn--primary" }, "Auditar");
  form.append(input, submitBtn);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const url = normalizeUrl(input.value);
    if (!url) return;
    const domain = url.replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
    window.location.hash = `#/audit/${encodeURIComponent(domain)}`;
  });

  const actions = el("div", { class: "hero__actions" }, [
    el("a", { class: "chip-btn", href: "#/demo" }, "▶ Ver demonstração"),
  ]);

  const engineStatus = el("div", { class: "engine-status" }, [
    el("span", { class: "dot" }),
    el("span", { id: "engine-status-text" }, "A verificar o motor de IA local…"),
  ]);

  inner.append(eyebrow, title, lead, form, actions, engineStatus);

  const recents = el("div", { class: "wrap recents" });
  const recentsTitle = el("h2", {}, "Auditorias recentes");
  const recentsGrid = el("div", { class: "recents-grid" });
  recents.append(recentsTitle, recentsGrid);

  section.appendChild(inner);
  root.append(section, recents);

  checkEngineStatus().then(({ backend, reachable }) => {
    const dot = engineStatus.querySelector(".dot");
    const text = engineStatus.querySelector("#engine-status-text");
    if (reachable === true) {
      dot.classList.add("is-ok");
      text.textContent = `${backend === "ollama" ? "Ollama" : backend} ligado`;
    } else if (reachable === false) {
      dot.classList.add("is-bad");
      text.innerHTML = "";
      text.append("Ollama não encontrado — ", el("a", { href: "https://ollama.com/download" }, "ver instruções"));
    } else {
      text.textContent = "Deteção do motor de IA disponível numa fase seguinte da construção.";
    }
  });

  const recentAudits = await fetchRecentAudits();
  if (recentAudits.length === 0) {
    recents.style.display = "none";
  } else {
    for (const audit of recentAudits) {
      recentsGrid.appendChild(
        el("a", { class: "card recent-card", href: `#/report/${encodeURIComponent(audit.domain)}/${encodeURIComponent(audit.audit_id)}` }, [
          el("div", { class: "recent-card__domain" }, audit.domain),
          el("div", { class: "recent-card__meta" }, [el("span", {}, audit.date), el("span", {}, `${audit.score}/100`)]),
        ])
      );
    }
  }
}
