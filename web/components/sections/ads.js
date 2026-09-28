import { el, copyToClipboard, showToast } from "../utils.js";

const GROUP_LABEL = {
  headlines: "Headlines",
  long_headlines: "Long headlines",
  descriptions: "Descriptions",
  primary_text: "Primary text",
};

function countLabel(chars, limits) {
  if (limits.max != null) return `${chars}/${limits.max}`;
  if (limits.min != null) return `${chars} (mín. ${limits.min})`;
  return `${chars}`;
}

function assetRow(item, limits = {}) {
  const label = countLabel(item.chars, limits);
  const row = el("div", { class: "ad-row" }, [
    el("div", { class: "ad-row__text" }, item.text),
    el("span", { class: `ad-row__count ${item.valid ? "is-ok" : "is-bad"}` }, label),
    el("button", { class: "btn btn--ghost btn--sm copy-btn", onclick: async () => { (await copyToClipboard(item.text)) && showToast("Copiado."); } }, "Copiar"),
  ]);
  if (!item.valid && item.issues?.length) {
    row.title = item.issues.join("; ");
  }
  return row;
}

function assetGroup(title, items, limits, extraCopyText) {
  const group = el("div", { class: "ads-group" });
  const validCount = items.filter((i) => i.valid).length;
  group.appendChild(
    el("div", { class: "ads-group__title" }, [
      title,
      el("span", { class: "count" }, `${validCount}/${items.length} válidos`),
    ])
  );
  items.forEach((item) => group.appendChild(assetRow(item, limits)));
  const copyAllBtn = el("button", { class: "btn btn--ghost btn--sm", style: "margin-top:4px" }, "Copiar tudo");
  copyAllBtn.addEventListener("click", async () => {
    const text = (extraCopyText ?? items.map((i) => i.text)).join("\n");
    (await copyToClipboard(text)) && showToast(`"${title}" copiado.`);
  });
  group.appendChild(copyAllBtn);
  return group;
}

function sitelinksGroup(sitelinks) {
  const group = el("div", { class: "ads-group" }, [el("div", { class: "ads-group__title" }, "Sitelinks")]);
  for (const sl of sitelinks) {
    const block = el("div", { class: "sitelink-block" }, [
      el("div", { class: "sitelink-block__text" }, [
        sl.text.text,
        el("span", { class: `ad-row__count ${sl.text.valid ? "is-ok" : "is-bad"}`, style: "margin-left:8px" }, `${sl.text.chars}/25`),
      ]),
      ...sl.descriptions.map((d) => assetRow(d, { max: 35 })),
    ]);
    group.appendChild(block);
  }
  return group;
}

function searchPreview(block, audit) {
  const headline = block.headlines[0]?.text ?? "";
  const desc = block.descriptions[0]?.text ?? "";
  const empresa = audit.profile?.empresa?.value ?? "";
  const displayUrl = (audit.profile?.landing_page_url?.value ?? audit.meta.url).replace(/^https?:\/\//i, "");
  return el("div", { class: "ad-preview ad-preview__search" }, [
    el("div", { class: "headline" }, empresa ? `${headline} | ${empresa}` : headline),
    el("div", { class: "url" }, displayUrl),
    el("div", { class: "desc" }, desc),
  ]);
}

function metaPreview(block) {
  return el("div", { class: "ad-preview ad-preview__meta" }, [
    el("div", { class: "swatch" }),
    el("div", {}, [
      el("div", { style: "font-weight:700" }, block.headlines[0]?.text ?? ""),
      el("div", { style: "font-size:0.85rem;color:#555" }, block.descriptions[0]?.text ?? ""),
      el("div", { style: "font-size:0.8rem;color:#888;margin-top:6px" }, (block.primary_text[0]?.text ?? "").slice(0, 125)),
    ]),
  ]);
}

export function renderAds(audit) {
  const ads = audit.ads;
  const keys = Object.keys(ads);
  const section = el("section", { class: "section-block", id: "sec-anuncios" });
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Anúncios"),
      el("h2", { class: "section-title" }, "Prontos para colar nas contas"),
      el("p", { class: "section-summary" }, `${audit.meta.ads_valid_count} de ${audit.meta.ads_total_count} assets dentro dos limites.`),
    ])
  );

  const tabs = el("div", { class: "ads-tabs" });
  const content = el("div");
  section.append(tabs, content);

  if (keys.length === 0) {
    content.appendChild(
      el("div", { class: "card" }, "Os anúncios ainda não foram gerados para esta auditoria (a geração liga-se ao pipeline real na Fase 6 desta construção).")
    );
    return section;
  }

  let activeKey = keys[0];

  function renderContent() {
    content.innerHTML = "";
    const block = ads[activeKey];
    const isMeta = Boolean(block.primary_text);

    if (block.primary_text) content.appendChild(assetGroup(GROUP_LABEL.primary_text, block.primary_text, {}));
    if (block.headlines) content.appendChild(assetGroup(GROUP_LABEL.headlines, block.headlines, { max: isMeta ? 40 : 30 }));
    if (block.long_headlines) content.appendChild(assetGroup(GROUP_LABEL.long_headlines, block.long_headlines, { min: 85, max: 90 }));
    if (block.descriptions) content.appendChild(assetGroup(GROUP_LABEL.descriptions, block.descriptions, isMeta ? { max: 30 } : { min: 85, max: 90 }));
    if (block.sitelinks) content.appendChild(sitelinksGroup(block.sitelinks));
    if (block.cta) content.appendChild(el("div", { class: "card", style: "margin-top:8px;font-size:0.85rem" }, [el("strong", {}, "CTA sugerido: "), block.cta]));

    content.appendChild(isMeta ? metaPreview(block) : searchPreview(block, audit));
  }

  for (const key of keys) {
    const tab = el("button", { class: `ads-tab${key === activeKey ? " is-active" : ""}` }, ads[key].label);
    tab.addEventListener("click", () => {
      activeKey = key;
      tabs.querySelectorAll(".ads-tab").forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      renderContent();
    });
    tabs.appendChild(tab);
  }
  renderContent();

  return section;
}
