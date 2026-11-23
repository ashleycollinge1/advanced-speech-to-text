// Transcription model catalogue (from /api/models), cached for the session,
// plus a reusable picker.
import { api } from "./api.js";
import { h } from "./dom.js";

let cache = null;
const STORE_KEY = "verbatim.model";

export async function getModels() {
  if (!cache) cache = api("/api/models").catch((err) => { cache = null; throw err; });
  return cache;
}

export function modelLabel(catalog, id) {
  const m = catalog?.models.find((x) => x.id === id);
  return m ? `${m.label} · ${m.id}` : id || "";
}

export function preferredModel(catalog) {
  const saved = localStorage.getItem(STORE_KEY);
  return catalog.models.some((m) => m.id === saved) ? saved : catalog.default;
}

export function rememberModel(id) {
  try { localStorage.setItem(STORE_KEY, id); } catch { /* private mode */ }
}

/**
 * Radio-pill picker. Models are ordered fastest → most accurate, and a small
 * meter shows relative accuracy within the configured list.
 */
export function modelPicker(catalog, { name, selected, onChange, legend = "Transcription quality" }) {
  const desc = h("p.model-desc", { "aria-live": "polite" });
  const total = catalog.models.length;
  const describe = (id) => {
    const m = catalog.models.find((x) => x.id === id);
    desc.replaceChildren(
      h("strong", m.label), " ", h("span.mono.faint", m.id), " · ", m.description || "",
      m.size ? h("span.faint", ` First use downloads about ${m.size}.`) : null,
    );
  };
  const options = catalog.models.map((m, i) => {
    const input = h("input", { type: "radio", name, value: m.id, checked: m.id === selected });
    input.addEventListener("change", () => { describe(m.id); onChange?.(m.id); });
    return h("label.model-opt",
      input,
      h("span.model-name", m.label, m.default ? h("span.model-default", "default") : null),
      h("span.model-meta",
        h("span.meter", { "aria-hidden": "true" },
          Array.from({ length: total }, (_, k) => h(`span${k <= i ? ".on" : ""}`))),
        h("span", m.speed || m.id)));
  });
  const el = h("fieldset.model-picker", h("legend.eyebrow", legend), h("div.model-options", options), desc);
  describe(selected);
  return {
    el,
    get value() { return el.querySelector("input:checked")?.value || catalog.default; },
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
