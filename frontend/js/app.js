import { api, configure, setCsrf, ApiError } from "./api.js";
import { h, clear, icon, toast } from "./dom.js";
import * as loginView from "./views/login.js";
import * as libraryView from "./views/library.js";
import * as recordingView from "./views/recording.js";
import * as searchView from "./views/search.js";
import * as adminView from "./views/admin.js";
import * as accountView from "./views/account.js";

const main = document.getElementById("main");
const topbar = document.getElementById("topbar");
const navLinks = document.getElementById("nav-links");

const session = { user: null, version: "" };
let cleanup = null;

const routes = [
  { pattern: /^\/login$/, view: loginView, public: true },
  { pattern: /^\/password$/, view: loginView, public: true, mode: "password" },
  { pattern: /^\/$/, view: libraryView, nav: "library" },
  { pattern: /^\/r\/([0-9a-f-]{36})$/, view: recordingView, nav: "library" },
  { pattern: /^\/search$/, view: searchView, nav: "search" },
  { pattern: /^\/admin$/, view: adminView, nav: "admin", admin: true },
  { pattern: /^\/account$/, view: accountView, nav: "account" },
];

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, qs = ""] = raw.split("?");
  return { path: path || "/", query: new URLSearchParams(qs) };
}

export function navigate(path, { replace = false } = {}) {
  const target = `#${path}`;
  if (location.hash === target) route();
  else if (replace) {
    history.replaceState(null, "", target);
    route();
  } else location.hash = target;
}

function renderNav(active) {
  clear(navLinks);
  if (!session.user) {
    topbar.hidden = true;
    return;
  }
  topbar.hidden = false;
  const items = [
    { id: "library", href: "#/", label: "Library", icon: "library" },
    { id: "search", href: "#/search", label: "Search", icon: "search" },
    session.user.role === "admin" && { id: "admin", href: "#/admin", label: "Users", icon: "users" },
    { id: "account", href: "#/account", label: session.user.username, icon: "user" },
  ].filter(Boolean);
  for (const it of items) {
    navLinks.append(
      h("li", h("a", { href: it.href, "aria-current": it.id === active ? "page" : null, title: it.label },
        icon(it.icon), h("span.label", it.label))),
    );
  }
  navLinks.append(
    h("li", h("button", { type: "button", title: "Sign out", onclick: signOut }, icon("logout"), h("span.label", "Sign out"))),
  );
}

async function signOut() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch { /* already signed out */ }
  session.user = null;
  setCsrf(null);
  navigate("/login");
}

async function loadSession() {
  try {
    const me = await api("/api/auth/me");
    session.user = me.user;
    session.version = me.version;
    setCsrf(me.csrf_token);
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) throw err;
    session.user = null;
  }
}

export function setSession(user, csrf) {
  session.user = user;
  if (csrf) setCsrf(csrf);
}

async function route() {
  const { path, query } = parseHash();
  let match = null;
  let params = [];
  for (const r of routes) {
    const m = path.match(r.pattern);
    if (m) { match = r; params = m.slice(1); break; }
  }
  if (!match) return navigate("/", { replace: true });

  if (!match.public && !session.user) return navigate("/login", { replace: true });
  if (session.user?.must_change_password && match.mode !== "password") return navigate("/password", { replace: true });
  if (match.public && session.user && !match.mode) return navigate("/", { replace: true });
  if (match.mode === "password" && !session.user) return navigate("/login", { replace: true });
  if (match.admin && session.user.role !== "admin") return navigate("/", { replace: true });

  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  document.body.classList.remove("has-dock");
  renderNav(match.nav);
  clear(main);
  window.scrollTo(0, 0);
  try {
    cleanup = (await match.view.render(main, {
      session, params, query, mode: match.mode, navigate, setSession, signOut,
    })) || null;
  } catch (err) {
    console.error(err);
    clear(main).append(
      h("section.page", h("div.error-state", { role: "alert" },
        h("h2", "Something went wrong"),
        h("p", err.message || String(err)),
        h("button.btn", { type: "button", onclick: () => route() }, icon("retry"), "Try again"))),
    );
  }
  // Move focus for screen readers on route change (not on first load).
  if (route.ran) main.focus({ preventScroll: true });
  route.ran = true;
}

configure({
  onAuthLost: () => {
    if (!session.user) return;
    session.user = null;
    toast("Your session ended. Please sign in again.", "error");
    navigate("/login");
  },
  onPasswordRequired: () => {
    if (session.user) session.user.must_change_password = true;
    navigate("/password");
  },
});

window.addEventListener("hashchange", route);

(async function boot() {
  try {
    await loadSession();
  } catch (err) {
    clear(main).append(h("section.page", h("div.error-state", { role: "alert" },
      h("h2", "Can't reach the server"),
      h("p", err.message),
      h("button.btn", { type: "button", onclick: () => location.reload() }, icon("retry"), "Reload"))));
    return;
  }
  route();
})();
