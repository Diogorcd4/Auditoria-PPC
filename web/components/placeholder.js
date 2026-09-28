import { el } from "./utils.js";

export function renderPlaceholder(root, title, message) {
  root.innerHTML = "";
  root.appendChild(
    el("div", { class: "wrap placeholder-screen" }, [
      el("h1", {}, title),
      el("p", {}, message),
      el("a", { class: "btn btn--ghost", href: "#/", style: "margin-top:20px;display:inline-flex" }, "Voltar ao início"),
    ])
  );
}

export function renderErrorCard(container, message, onRetry) {
  container.innerHTML = "";
  const retryBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Tentar de novo");
  retryBtn.addEventListener("click", onRetry);
  container.appendChild(el("div", { class: "wrap", style: "padding:60px 0" }, el("div", { class: "error-card" }, [el("span", { class: "error-card__msg" }, message), retryBtn])));
}
