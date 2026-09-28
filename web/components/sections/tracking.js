import { el } from "../utils.js";

const MONOGRAM = {
  ga4: "GA4",
  ua: "UA",
  gtm: "GTM",
  google_ads: "AW",
  meta_pixel: "FB",
  microsoft_uet: "UET",
};

const STATE_TONE = {
  "A disparar sem consentimento": "bad",
  "A disparar só após consentimento": "ok",
  "No código, sem disparo observado": "warn",
  "Não detetado": "neutral",
  Detetado: "ok",
  "Não verificável": "neutral",
};

function consentCompare(state) {
  if (/sem consentimento/i.test(state)) return ["Dispara", "Dispara"];
  if (/só após consentimento/i.test(state)) return ["Não dispara", "Dispara"];
  if (/sem disparo observado/i.test(state)) return ["Não dispara", "Não dispara"];
  return null;
}

function trackingCard(key, entry) {
  const tone = STATE_TONE[entry.state] ?? "neutral";
  const card = el("div", { class: "card tracking-card" });

  const head = el("div", { class: "tracking-card__head" }, [
    el("div", { class: "tracking-monogram" }, MONOGRAM[key] ?? entry.platform.slice(0, 3).toUpperCase()),
    el("div", { style: "flex:1" }, [
      el("div", { class: "tracking-card__title" }, entry.platform),
      el("span", { class: `badge badge--${tone}`, style: "margin-top:4px" }, entry.state),
    ]),
  ]);
  card.appendChild(head);

  if (entry.id) {
    card.appendChild(el("div", { class: "tracking-card__ids" }, [el("span", { class: "mono-chip" }, entry.id)]));
  }

  const compare = key !== "gtm" ? consentCompare(entry.state) : null;
  if (compare) {
    card.appendChild(
      el("div", { class: "tracking-consent-compare" }, [
        el("div", {}, [el("strong", {}, "Antes do consentimento"), compare[0]]),
        el("div", {}, [el("strong", {}, "Depois do consentimento"), compare[1]]),
      ])
    );
  }

  if (entry.evidence && entry.evidence.length > 0) {
    const list = el(
      "ul",
      { class: "evidence-list", style: "display:none" },
      entry.evidence.map((line) => el("li", {}, line))
    );
    const toggle = el("button", { class: "evidence-toggle" }, `Ver evidência (${entry.evidence.length})`);
    toggle.addEventListener("click", () => {
      const showing = list.style.display !== "none";
      list.style.display = showing ? "none" : "flex";
      list.style.flexDirection = "column";
      toggle.textContent = showing ? `Ver evidência (${entry.evidence.length})` : "Ocultar evidência";
    });
    card.append(toggle, list);
  }

  if (entry.note) card.appendChild(el("div", { style: "color:var(--muted);font-size:0.82rem" }, entry.note));
  if (entry.container_contents_inferred) {
    card.appendChild(
      el("div", { style: "font-size:0.8rem;color:var(--muted)" }, [
        "Carrega (inferido do container): ",
        entry.container_contents_inferred.join(", "),
      ])
    );
  }

  return card;
}

export function renderTracking(audit) {
  const t = audit.tracking;
  const section = el("section", { class: "section-block", id: "sec-tracking" });
  const detectedCount = audit.meta.tracking_platforms_detected;
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Tracking"),
      el("h2", { class: "section-title" }, "O que já está instalado"),
      el("p", { class: "section-summary" }, `${detectedCount} de ${audit.meta.tracking_platforms_total} plataformas detetadas.`),
    ])
  );

  const grid = el("div", { class: "grid-3" });
  for (const key of ["ga4", "ua", "gtm", "google_ads", "meta_pixel", "microsoft_uet"]) {
    if (t[key]) grid.appendChild(trackingCard(key, t[key]));
  }
  section.appendChild(grid);

  if (t.extras?.length) {
    const extrasCard = el("div", { class: "card", style: "margin-top:16px" }, [
      el("h3", { style: "font-size:0.9rem;margin-bottom:10px" }, "Extras"),
      el(
        "div",
        { style: "display:flex;gap:8px;flex-wrap:wrap" },
        t.extras.map((extra) => el("span", { class: `badge badge--${STATE_TONE[extra.state] ?? "neutral"}` }, `${extra.platform}: ${extra.state}`))
      ),
    ]);
    section.appendChild(extrasCard);
  }

  section.appendChild(el("div", { class: "not-visible-note" }, [el("strong", { style: "display:block;margin-bottom:4px;color:var(--text)" }, "O que a ferramenta não consegue ver"), t.not_visible_note]));

  return section;
}
