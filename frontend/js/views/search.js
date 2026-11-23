import { api } from "../api.js";
import { h, clear, icon, reveal, clock, skeletonLines, autofocus } from "../dom.js";

/** The server marks matches with \x02…\x03; turn them into <mark> safely. */
function snippet(text) {
  const out = [];
  const parts = text.split(/(\x02[^\x03]*\x03)/);
  for (const p of parts) {
    if (p.startsWith("\x02")) out.push(h("mark", p.slice(1, -1)));
    else if (p) out.push(p);
  }
  return out;
}

export function render(main, ctx) {
  const q = (ctx.query.get("q") || "").trim();
  const input = h("input.input.search-input#q", {
    type: "search", name: "q", value: q, placeholder: "Search every transcript…",
    autocomplete: "off", "aria-label": "Search transcripts", maxlength: 200,
  });
  const form = h("form.search-form", { role: "search" }, icon("search"), input,
    h("button.btn.btn-primary", { type: "submit" }, "Search"));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const v = input.value.trim();
    ctx.navigate(v ? `/search?q=${encodeURIComponent(v)}` : "/search");
  });

  const head = h("header.page-head",
    h("p.eyebrow", "Search"),
    h("h1.display", h("span.thin", "Find the "), "moment", h("span.accent", ".")),
    h("p.muted", "Matches words across all your transcripts, including stems: “plan” also finds “planning”. Use quotes for exact phrases and - to exclude."));
  const results = h("section.results", { "aria-live": "polite", "aria-label": "Search results" });
  reveal(head, form, results);
  main.append(h("div.page", head, form, results));
  autofocus(input, () => input.setSelectionRange(input.value.length, input.value.length));

  let aborted = false;
  if (!q) {
    results.append(h("div.empty",
      h("p.empty-mark", { "aria-hidden": "true" }, "⌕"),
      h("h3", "Search everything you've recorded"),
      h("p", "Results link straight to the moment in the recording, ready to play.")));
    return;
  }

  results.append(...[0, 1].map(() => h("div.panel.result", { "aria-hidden": "true" }, h("span.skeleton.skeleton-line.w-30"), skeletonLines(["w-85", "w-70"]))));
  results.setAttribute("aria-busy", "true");

  api(`/api/search?q=${encodeURIComponent(q)}`).then((res) => {
    if (aborted) return;
    clear(results).removeAttribute("aria-busy");
    if (!res.results.length) {
      results.append(h("div.empty",
        h("p.empty-mark", { "aria-hidden": "true" }, "0"),
        h("h3", `Nothing matches “${q}”`),
        h("p", "Try fewer or different words. Search ignores very common words like “the”.")));
      return;
    }
    results.append(h("p.eyebrow", `${res.total_hits} match${res.total_hits === 1 ? "" : "es"} in ${res.results.length} recording${res.results.length === 1 ? "" : "s"}`));
    for (const r of res.results) {
      results.append(h("article.panel.result",
        h("h2", h("a", { href: `#/r/${r.recording_id}` }, r.title)),
        h("ul.hit-list", r.hits.map((hit) =>
          h("li", h("a.hit", { href: `#/r/${r.recording_id}?t=${hit.start}` },
            h("span.mono", clock(hit.start)), h("span", snippet(hit.snippet))))))));
    }
  }).catch((err) => {
    if (aborted) return;
    clear(results).removeAttribute("aria-busy");
    results.append(h("div.error-state", { role: "alert" },
      h("h2", "Search failed"), h("p", err.message),
      h("button.btn", { type: "button", onclick: () => ctx.navigate(`/search?q=${encodeURIComponent(q)}`) }, icon("retry"), "Retry")));
  });

  return () => { aborted = true; };
}
