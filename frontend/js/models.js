// Transcription model catalogue (from /api/models) with download state, plus a
// picker that lets users download models explicitly. Transcription only ever
// uses models already on the server, so it runs offline.
import { api } from "./api.js";
import { h, icon, dialog, toast, bytes } from "./dom.js";

let cache = null;
const STORE_KEY = "verbatim.model";
const POLL_MS = 1500;

/** Catalogue + download state. `fresh` bypasses the session cache (status changes). */
export async function getModels({ fresh = false } = {}) {
  if (!cache || fresh) cache = api("/api/models").catch((err) => { cache = null; throw err; });
  return cache;
}

export function modelLabel(catalog, id) {
  const m = catalog?.models.find((x) => x.id === id);
  return m ? `${m.label} · ${m.id}` : id || "";
}

/** The user's last choice if it's downloaded, else the default if downloaded, else any downloaded model. */
export function preferredModel(catalog, preferred) {
  const ready = catalog.models.filter((m) => m.ready).map((m) => m.id);
  let saved = null;
  try { saved = localStorage.getItem(STORE_KEY); } catch { /* private mode */ }
  for (const id of [preferred, saved, catalog.default]) if (id && ready.includes(id)) return id;
  return ready[0] || null;
}

export function rememberModel(id) {
  try { localStorage.setItem(STORE_KEY, id); } catch { /* private mode */ }
}

const isBusy = (m) => m.status === "queued" || m.status === "downloading";
const sizeOf = (m) => (m.bytes_total ? bytes(m.bytes_total) : m.size || "");

async function confirmDownload(m) {
  return dialog({
    title: `Download “${m.label}”?`,
    body: h("div.form",
      h("p", h("strong", `${m.id}`), sizeOf(m) ? ` · about ${sizeOf(m)}` : ""),
      h("p.muted", "The model is downloaded once from Hugging Face onto this server. After that, transcription with it runs entirely offline: no audio or text ever leaves the server."),
      h("p.muted", "You can keep using the app while it downloads.")),
    actions: [{ label: "Cancel", value: false }, { label: "Download", value: true, primary: true }],
  });
}

/**
 * Model picker. Downloaded models can be selected; the rest show a Download
 * button, then live progress. Polls while downloads are in flight.
 * Returns { el, value, destroy() }. `value` is null if nothing is downloaded.
 */
export function modelPicker(catalog, { name, selected, onChange, onReadyChange, legend = "Transcription quality" }) {
  let models = catalog.models;
  let current = preferredModel(catalog, selected);
  let timer = null;
  let destroyed = false;
  const total = models.length;

  const desc = h("p.model-desc", { "aria-live": "polite" });
  const banner = h("div.model-banner", { role: "status" });
  const grid = h("div.model-options");
  const el = h("fieldset.model-picker", h("legend.eyebrow", legend), banner, grid, desc);
  const nodes = new Map();

  function describe() {
    const m = models.find((x) => x.id === current);
    if (!m) {
      desc.replaceChildren(h("span.faint", "Choose a downloaded model."));
      return;
    }
    desc.replaceChildren(h("strong", m.label), " ", h("span.mono.faint", m.id), " · ", m.description || "",
      h("span.faint", " Runs offline on this server."));
  }

  function renderBanner() {
    const anyReady = models.some((m) => m.ready);
    const busy = models.filter(isBusy);
    banner.replaceChildren();
    banner.className = "model-banner";
    if (!anyReady) {
      banner.classList.add("is-empty");
      banner.append(icon("download"), h("div",
        h("strong", busy.length ? "Downloading your first model…" : "Download a model to start transcribing"),
        h("p", busy.length
          ? "Uploads and recording unlock as soon as it finishes. Keep this page open to watch progress, or come back later."
          : "Nothing is downloaded automatically. Pick a model below and press Download. It's fetched once, then everything runs offline.")));
    } else if (busy.length) {
      banner.classList.add("is-busy");
      banner.append(h("span.spinner", { "aria-hidden": "true" }),
        h("span", `Downloading ${busy.map((m) => m.label).join(", ")}…`));
    }
  }

  function statusArea(m) {
    if (m.ready) return h("span.model-state.is-ready", icon("check"), "On server");
    if (m.status === "queued") {
      return h("div.model-dl",
        h("div.progress.is-indeterminate", { role: "progressbar", "aria-label": `${m.label}: queued` }, h("span")),
        h("span.model-state", "Queued, starting shortly…"));
    }
    if (m.status === "downloading") {
      const pct = m.bytes_total ? Math.min(100, (m.bytes_done / m.bytes_total) * 100) : null;
      return h("div.model-dl",
        h(`div.progress${pct == null ? ".is-indeterminate" : ""}`, {
          role: "progressbar", "aria-label": `Downloading ${m.label}`,
          "aria-valuenow": pct == null ? null : Math.round(pct), "aria-valuemin": 0, "aria-valuemax": 100,
        }, h("span", { style: { "--p": `${pct ?? 0}%` } })),
        h("span.model-state.mono", pct == null
          ? `Downloading… ${bytes(m.bytes_done)}`
          : `${Math.floor(pct)}% · ${bytes(m.bytes_done)} of ${bytes(m.bytes_total)}`));
    }
    const btn = h("button.btn.btn-sm", { type: "button" }, icon("download"),
      m.status === "error" ? "Retry download" : `Download${sizeOf(m) ? ` · ${sizeOf(m)}` : ""}`);
    btn.addEventListener("click", () => startDownload(m, btn));
    return h("div.model-dl",
      m.status === "error" && m.error && h("span.model-state.is-error", m.error),
      btn);
  }

  function renderOption(m, i) {
    const input = h("input", { type: "radio", name, value: m.id, checked: m.id === current, disabled: !m.ready });
    input.addEventListener("change", () => {
      current = m.id;
      describe();
      onChange?.(m.id);
    });
    const node = h(`div.model-opt${m.ready ? "" : ".is-unavailable"}`,
      h("label.model-main",
        input,
        h("span.model-name", m.label, m.default ? h("span.model-default", "default") : null),
        h("span.model-meta",
          h("span.meter", { "aria-hidden": "true" },
            Array.from({ length: total }, (_, k) => h(`span${k <= i ? ".on" : ""}`))),
          h("span", m.speed || m.id))),
      statusArea(m));
    return node;
  }

  function render(prev) {
    models.forEach((m, i) => {
      const old = nodes.get(m.id);
      const p = prev?.get(m.id);
      // Only rebuild options whose state changed, so focus and clicks aren't disturbed.
      if (old && p && p.status === m.status && p.bytes_done === m.bytes_done && p.error === m.error) return;
      const node = renderOption(m, i);
      if (old) old.replaceWith(node); else grid.append(node);
      nodes.set(m.id, node);
    });
    renderBanner();
    describe();
  }

  async function refresh() {
    let fresh;
    try {
      fresh = await getModels({ fresh: true });
    } catch {
      return schedule(); // transient: keep polling
    }
    if (destroyed) return;
    const prev = new Map(models.map((m) => [m.id, m]));
    const wasReady = models.some((m) => m.ready);
    models = fresh.models;
    for (const m of models) {
      const p = prev.get(m.id);
      if (p && !p.ready && m.ready) toast(`“${m.label}” is downloaded and ready to use.`);
      if (p && p.status !== "error" && m.status === "error") toast(`Couldn't download “${m.label}”: ${m.error || "unknown error"}`, "error");
    }
    if (!current || !models.find((m) => m.id === current)?.ready) {
      const next = preferredModel(fresh, current);
      if (next !== current) { current = next; onChange?.(current); }
    }
    render(prev);
    const nowReady = models.some((m) => m.ready);
    if (nowReady !== wasReady) onReadyChange?.(nowReady);
    schedule();
  }

  function schedule() {
    clearTimeout(timer);
    if (!destroyed && models.some(isBusy)) timer = setTimeout(refresh, POLL_MS);
  }

  async function startDownload(m, btn) {
    if (!(await confirmDownload(m))) return;
    btn.disabled = true;
    try {
      await api(`/api/models/${encodeURIComponent(m.id)}/download`, { method: "POST" });
    } catch (err) {
      btn.disabled = false;
      toast(err.message, "error");
      return;
    }
    toast(`Downloading “${m.label}”…`);
    refresh();
  }

  render();
  schedule();
  onReadyChange?.(models.some((m) => m.ready));
  return {
    el,
    get value() { return current; },
    destroy() { destroyed = true; clearTimeout(timer); },
  };
}

// ---------------------------------------------------------------------------
// Vocabulary hints. Mirrors the server's normalisation for the live counter;
// the server stays authoritative and returns a clear error if over the limit.

export const VOCAB_MAX_CHARS = 400;

export function normalizeVocab(raw) {
  const seen = new Set();
  const out = [];
  for (const part of (raw || "").split(/[,;\n\r\t]+/)) {
    const term = part.replace(/\s+/g, " ").replace(/^[ .]+|[ .]+$/g, "");
    const key = term.toLocaleLowerCase();
    if (term && !seen.has(key)) { seen.add(key); out.push(term); }
  }
  return out.join(", ");
}

export function vocabField({ id, value = "", label = "Names & terms (optional)" }) {
  const ta = h(`textarea.textarea.vocab-input#${id}`, {
    value, rows: 2, maxlength: VOCAB_MAX_CHARS * 4, spellcheck: "false",
    placeholder: "e.g. Claude, Anthropic, Siobhán, Kubernetes, OKRs",
    "aria-describedby": `${id}-hint ${id}-count`,
  });
  const count = h(`span.vocab-count.mono#${id}-count`, { "aria-live": "polite" });
  const update = () => {
    const n = normalizeVocab(ta.value).length;
    count.textContent = `${n} / ${VOCAB_MAX_CHARS}`;
    const over = n > VOCAB_MAX_CHARS;
    count.classList.toggle("is-over", over);
    ta.setAttribute("aria-invalid", String(over));
  };
  ta.addEventListener("input", update);
  update();
  const el = h("div.field.vocab-field",
    h("div.vocab-label", h("label", { for: id }, label), count),
    ta,
    h(`p.hint#${id}-hint`,
      "Spell out names, acronyms and jargon that are said in the recording, separated by commas. ",
      "They nudge the transcriber towards your spelling. Keep the list short and relevant: unrelated words can creep into the transcript."));
  return {
    el,
    get value() { return normalizeVocab(ta.value); },
    get valid() { return normalizeVocab(ta.value).length <= VOCAB_MAX_CHARS; },
    clear() { ta.value = ""; update(); },
  };
}
