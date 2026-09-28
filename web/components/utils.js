export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function shortPageLabel(page) {
  if (!page?.title) return page?.id ?? "";
  return page.title.split(/\s+—\s+/)[0].trim();
}

export function formatDuration(seconds) {
  const s = Math.round(seconds || 0);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m === 0) return `${rem}s`;
  return `${m}m ${rem}s`;
}

export function formatDate(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("pt-PT", { day: "2-digit", month: "2-digit", year: "numeric" }) +
      " às " + d.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function showToast(message) {
  const root = document.getElementById("toast-root");
  if (!root) return;
  const toast = el("div", { class: "toast" }, message);
  root.appendChild(toast);
  setTimeout(() => toast.remove(), 3200);
}
