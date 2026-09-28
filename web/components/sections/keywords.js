import { el } from "../utils.js";

const STAGE_LABEL = { problema: "Problema", solucao: "Solução", comparacao: "Comparação", decisao: "Decisão", transacional_local: "Transacional / Local" };

export function renderKeywords(audit) {
  const kw = audit.keywords;
  const section = el("section", { class: "section-block", id: "sec-termos" });
  section.append(
    el("div", { class: "section-head" }, [
      el("div", { class: "eyebrow" }, "Termos de pesquisa"),
      el("h2", { class: "section-title" }, "O que o público mais qualificado procura"),
      el("p", { class: "section-summary" }, kw.note),
    ])
  );

  let showObserved = true;
  let showInferred = true;
  const toggleWrap = el("div", { class: "kw-toggle", style: "display:flex;gap:8px" });
  const btnObserved = el("button", { class: "filter-pill is-active" }, "● Observado (Google Autocomplete)");
  const btnInferred = el("button", { class: "filter-pill is-active" }, "◌ Inferido (agrupado por IA)");
  toggleWrap.append(btnObserved, btnInferred);
  section.appendChild(toggleWrap);

  const observedCard = el("div", { class: "card", style: "margin-top:16px" }, [
    el("h3", { style: "font-size:0.9rem;margin-bottom:10px" }, "Observado"),
    el(
      "div",
      { class: "kw-chips" },
      kw.observed.map((item) => el("span", { class: "kw-chip" }, item.term))
    ),
  ]);

  const inferredCard = el("div", { class: "card", style: "margin-top:16px" });
  inferredCard.appendChild(el("h3", { style: "font-size:0.9rem;margin-bottom:10px" }, "Inferido, por etapa de intenção"));
  for (const [stage, terms] of Object.entries(kw.inferred)) {
    inferredCard.appendChild(
      el("div", { class: "kw-stage" }, [
        el("h4", {}, STAGE_LABEL[stage] ?? stage),
        el(
          "div",
          { class: "kw-chips" },
          terms.map((term) => el("span", { class: "kw-chip kw-chip--inferred" }, term))
        ),
      ])
    );
  }

  section.append(observedCard, inferredCard);

  btnObserved.addEventListener("click", () => {
    showObserved = !showObserved;
    btnObserved.classList.toggle("is-active", showObserved);
    observedCard.style.display = showObserved ? "" : "none";
  });
  btnInferred.addEventListener("click", () => {
    showInferred = !showInferred;
    btnInferred.classList.toggle("is-active", showInferred);
    inferredCard.style.display = showInferred ? "" : "none";
  });

  if (kw.gaps?.length) {
    section.appendChild(
      el("div", { class: "card kw-gaps", style: "margin-top:16px" }, [
        el("h3", { style: "font-size:0.9rem;margin-bottom:10px" }, "Lacunas de conteúdo"),
        el("ul", { style: "display:flex;flex-direction:column;gap:8px" }, kw.gaps.map((g) => el("li", { style: "font-size:0.85rem;color:var(--muted)" }, g))),
      ])
    );
  }

  if (kw.negatives?.length) {
    section.appendChild(
      el("div", { class: "card kw-negatives", style: "margin-top:16px" }, [
        el("h3", { style: "font-size:0.9rem;margin-bottom:10px" }, "Palavras-chave negativas sugeridas"),
        el("div", {}, kw.negatives.map((n) => el("span", { class: "mono-chip" }, n))),
      ])
    );
  }

  return section;
}
