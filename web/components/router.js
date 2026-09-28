const routes = [];

export function registerRoute(pattern, handler) {
  routes.push({ pattern, handler });
}

function matchRoute(path) {
  for (const { pattern, handler } of routes) {
    const paramNames = [];
    const regexStr = pattern.replace(/:[^/]+/g, (m) => {
      paramNames.push(m.slice(1));
      return "([^/]+)";
    });
    const match = path.match(new RegExp(`^${regexStr}$`));
    if (match) {
      const params = {};
      paramNames.forEach((name, i) => (params[name] = decodeURIComponent(match[i + 1])));
      return { handler, params };
    }
  }
  return null;
}

function currentPath() {
  const hash = window.location.hash || "#/";
  return hash.slice(1) || "/";
}

function updateActiveNav(path) {
  document.querySelectorAll(".site-nav a").forEach((a) => {
    a.classList.toggle("is-active", a.dataset.route === path || (a.dataset.route !== "/" && path.startsWith(a.dataset.route)));
  });
}

export function startRouter() {
  const dispatch = () => {
    const path = currentPath();
    updateActiveNav(path);
    const match = matchRoute(path);
    const app = document.getElementById("app");
    app.scrollTo?.(0, 0);
    window.scrollTo(0, 0);
    if (match) {
      match.handler(match.params);
    } else {
      app.innerHTML = "";
    }
  };
  window.addEventListener("hashchange", dispatch);
  dispatch();
}
