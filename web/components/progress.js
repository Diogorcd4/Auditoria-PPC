import { el, formatDuration } from "./utils.js";

const STEPS = [
  { key: "crawl", label: "Site" },
  { key: "tracking", label: "Tracking" },
  { key: "comunicacao", label: "Comunicação" },
  { key: "sintese", label: "Síntese" },
  { key: "termos", label: "Termos" },
  { key: "perfil", label: "Perfil" },
  { key: "anuncios", label: "Anúncios" },
];

export function renderProgressSteps(audit) {
  const wrap = el("div", { class: "progress-steps" });
  for (const step of STEPS) {
    wrap.appendChild(el("div", { class: "progress-step is-done" }, [el("span", { class: "dot" }), step.label]));
  }
  const meta = el("div", { class: "progress-meta" }, [
    el("span", {}, `Concluída em ${formatDuration(audit.meta.duration_seconds)}`),
  ]);
  wrap.appendChild(meta);
  return wrap;
}

export function renderStats(audit) {
  const { meta } = audit;
  const cells = [
    { value: `${meta.communication_score}`, label: "Pontuação de comunicação" },
    { value: `${meta.tracking_platforms_detected} de ${meta.tracking_platforms_total}`, label: "Plataformas de tracking" },
    { value: `${meta.opportunities_count}`, label: "Oportunidades de venda" },
    { value: `${meta.pages_analyzed}`, label: "Páginas analisadas" },
    { value: `${meta.ads_valid_count} de ${meta.ads_total_count}`, label: "Anúncios válidos" },
  ];
  return el(
    "div",
    { class: "stats" },
    cells.map((cell) => el("div", { class: "stats__cell" }, [el("div", { class: "stats__value" }, cell.value), el("div", { class: "stats__label" }, cell.label)]))
  );
}
