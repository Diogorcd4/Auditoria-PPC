import { el } from "./utils.js";

export function renderErrorCard(container, message, onRetry) {
  container.innerHTML = "";
  const retryBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Tentar de novo");
  retryBtn.addEventListener("click", onRetry);
  container.appendChild(el("div", { class: "wrap", style: "padding:60px 0" }, el("div", { class: "error-card" }, [el("span", { class: "error-card__msg" }, message), retryBtn])));
}
