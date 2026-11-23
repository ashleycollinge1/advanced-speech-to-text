// Tiny DOM toolkit. All text goes through textContent, never innerHTML, so
// transcript or user content can't inject markup.

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * h("button.btn.btn-primary", { onclick, type: "button" }, "Save")
 * Tag shorthand supports .class and #id. Children may be strings, nodes,
 * arrays, null/false (skipped).
 */
export function h(tag, attrs, ...children) {
  if (attrs == null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs);
    attrs = {};
  }
  const [, name = "div", rest = ""] = tag.match(/^([a-z0-9-]*)(.*)$/i);
  const el = document.createElement(name || "div");
  for (const part of rest.match(/[.#][^.#]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.classList.add(...String(v).split(/\s+/).filter(Boolean));
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
    else if (k === "ref") v(el);
    else if (k === "value") el.value = v;
    else if (k in el && typeof v !== "string" && k !== "list") el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

/** Like Element.append, but skips null/false/undefined (native append prints them). */
export function append(el, ...children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

/** Mark children for the one orchestrated entrance per view. */
export function reveal(...els) {
  els.flat().forEach((el, i) => {
    if (!el) return;
    el.classList.add("reveal");
    el.style.setProperty("--i", String(i));
  });
}

/**
 * Autofocus after the router has settled, but never steal focus from a field
 * the user (or a password manager) is already typing into.
 */
export function autofocus(el, after) {
  setTimeout(() => {
    const active = document.activeElement;
    if (active && active !== document.body && active.id !== "main") return;
    el.focus();
    after?.();
  }, 50);
}

// --------------------------------------------------------------- icons

const ICONS = {
  play: "M7 4.5v15l12.5-7.5z",
  pause: "M7 4h3.5v16H7zM13.5 4H17v16h-3.5z",
  upload: "M12 16V4m0 0L7 9m5-5 5 5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  mic: "M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm6-3a6 6 0 0 1-12 0m6 6v3",
  stop: "M6 6h12v12H6z",
  search: "m20 20-4.5-4.5M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13z",
  pencil: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  trash: "M5 7h14M10 7V4h4v3m-7 0 1 13h8l1-13",
  download: "M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19h14",
  bookmark: "M7 4h10v16l-5-3.5L7 20z",
  x: "M6 6l12 12M18 6 6 18",
  check: "m5 12.5 4.5 4.5L19 7.5",
  retry: "M4 12a8 8 0 1 0 2.4-5.7M4 4v4.5h4.5",
  back: "M15 5l-7 7 7 7",
  users: "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 10.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 19v-1a4 4 0 0 0-3-3.9M15.5 3.6a3.5 3.5 0 0 1 0 6.8",
  user: "M19 20v-1a5 5 0 0 0-5-5h-4a5 5 0 0 0-5 5v1M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  logout: "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10",
  library: "M4 5h4v15H4zM10 5h4v15h-4zM16.5 5.5l3.5-1 3 14.5-3.5 1z",
  alert: "M12 8v5m0 3.5v.01M10.3 3.9 2.5 18a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  copy: "M9 9h10v11H9zM5 15V4h10",
  back5: "M11 6 5 12l6 6M19 6l-6 6 6 6",
  fwd5: "m13 6 6 6-6 6M5 6l6 6-6 6",
};

export function icon(name, label) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", name === "play" || name === "pause" || name === "stop" ? "currentColor" : "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", name === "play" || name === "pause" || name === "stop" ? "0" : "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || "");
  svg.append(path);
  return svg;
}

// --------------------------------------------------------------- formatting

export function clock(seconds, withHours = false) {
  const t = Math.max(0, Math.floor(seconds || 0));
  const hh = Math.floor(t / 3600);
  const mm = Math.floor((t % 3600) / 60);
  const ss = String(t % 60).padStart(2, "0");
  return hh || withHours ? `${hh}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

export function humanDuration(seconds) {
  if (seconds == null) return "—";
  const t = Math.round(seconds);
  if (t < 60) return `${t}s`;
  const hh = Math.floor(t / 3600);
  const mm = Math.floor((t % 3600) / 60);
  return hh ? `${hh}h ${String(mm).padStart(2, "0")}m` : `${mm}m ${String(t % 60).padStart(2, "0")}s`;
}

export function bytes(n) {
  if (!n && n !== 0) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${u[i]}`;
}

const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
export function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${dateFmt.format(d)} · ${timeFmt.format(d)}`;
}

// --------------------------------------------------------------- feedback

export function toast(message, kind = "ok", ms = 4200) {
  const host = document.getElementById("toasts");
  const el = h(`div.toast.toast-${kind}`, icon(kind === "error" ? "alert" : "check"), h("span", message));
  host.append(el);
  setTimeout(() => {
    el.classList.add("is-leaving");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  }, ms);
}

export function formError(message) {
  return h("div.form-error", { role: "alert" }, icon("alert"), h("span", message));
}

export function spinner() {
  return h("span.spinner", { "aria-hidden": "true" });
}

/** Disable a button and show a spinner while an async action runs. */
export async function busy(button, fn) {
  const prev = Array.from(button.childNodes);
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  clear(button).append(spinner(), h("span", button.dataset.busyLabel || "Working…"));
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    clear(button).append(...prev);
  }
}

export function skeletonLines(widths = ["w-70", "w-100", "w-85", "w-40"]) {
  return widths.map((w) => h(`span.skeleton.skeleton-line.${w}`));
}

/** Modal dialog helper returning a promise. */
export function dialog({ title, body, actions }) {
  return new Promise((resolve) => {
    const dlg = h("dialog", { "aria-labelledby": "dlg-title" });
    const close = (value) => {
      dlg.close();
      dlg.remove();
      resolve(value);
    };
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(null); });
    const buttons = actions.map((a) =>
      h(`button.btn${a.primary ? ".btn-primary" : ""}${a.danger ? ".btn-danger" : ""}`, {
        type: "button",
        onclick: () => close(a.value),
      }, a.label),
    );
    dlg.append(h("div.dialog-body", h("h2#dlg-title", title), body, h("div.dialog-actions", buttons)));
    document.body.append(dlg);
    dlg.showModal();
    (buttons.find((b, i) => actions[i].primary) || buttons[0])?.focus();
  });
}

export async function confirmDialog(title, message, confirmLabel = "Delete") {
  return dialog({
    title,
    body: h("p.muted", message),
    actions: [
      { label: "Cancel", value: false },
      { label: confirmLabel, value: true, primary: true },
    ],
  });
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch {
    toast("Couldn't copy: select the text and copy it manually", "error");
  }
}
