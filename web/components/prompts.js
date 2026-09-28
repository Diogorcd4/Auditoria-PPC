import { el, showToast } from "./utils.js";
import { fetchPrompts, resetPrompt, savePrompt } from "./state.js";

export async function renderPromptsEditor(root) {
  root.innerHTML = "";
  const wrap = el("div", { class: "wrap", style: "padding:48px 0" });
  wrap.append(
    el("div", { class: "eyebrow" }, "Prompts"),
    el("h1", { class: "section-title", style: "margin-bottom:4px" }, "Os 7 templates de anúncios"),
    el("p", { class: "section-summary", style: "margin-bottom:24px" }, "Editáveis à vontade. \"Repor original\" traz de volta o texto de fábrica.")
  );
  root.appendChild(wrap);

  let prompts;
  try {
    prompts = await fetchPrompts();
  } catch {
    wrap.appendChild(el("div", { class: "error-card" }, el("span", { class: "error-card__msg" }, "Não foi possível carregar os prompts.")));
    return;
  }

  for (const [name, entry] of Object.entries(prompts)) {
    const card = el("div", { class: "card", style: "margin-bottom:20px" });
    const badge = el("span", { class: `badge ${entry.is_default ? "badge--neutral" : "badge--info"}` }, entry.is_default ? "Original" : "Editado");
    const head = el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:12px" }, [
      el("h3", { style: "font-size:0.95rem" }, name),
      badge,
    ]);
    const textarea = el("textarea", {
      style: "width:100%;min-height:220px;background:var(--bg-2);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:12px;font-family:monospace;font-size:0.8rem;line-height:1.5",
    });
    textarea.value = entry.content;

    const actions = el("div", { style: "display:flex;gap:8px;margin-top:12px" });
    const saveBtn = el("button", { class: "btn btn--primary btn--sm" }, "Guardar");
    const resetBtn = el("button", { class: "btn btn--ghost btn--sm" }, "Repor original");
    actions.append(saveBtn, resetBtn);

    saveBtn.addEventListener("click", async () => {
      try {
        const result = await savePrompt(name, textarea.value);
        badge.textContent = result.is_default ? "Original" : "Editado";
        badge.className = `badge ${result.is_default ? "badge--neutral" : "badge--info"}`;
        showToast(`"${name}" guardado.`);
      } catch (err) {
        showToast(err.message || "Não foi possível guardar.");
      }
    });

    resetBtn.addEventListener("click", async () => {
      try {
        const result = await resetPrompt(name);
        textarea.value = result.content;
        badge.textContent = "Original";
        badge.className = "badge badge--neutral";
        showToast(`"${name}" reposto.`);
      } catch (err) {
        showToast(err.message || "Não foi possível repor.");
      }
    });

    card.append(head, textarea, actions);
    wrap.appendChild(card);
  }
}
