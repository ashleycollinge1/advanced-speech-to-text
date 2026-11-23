import { api } from "../api.js";
import {
  h, clear, icon, reveal, clock, humanDuration, when, toast, busy, dialog, confirmDialog,
  skeletonLines, formError, copyText,
} from "../dom.js";
import { statusChip } from "./library.js";
import { getModels, modelLabel, modelPicker, vocabField, VOCAB_MAX_CHARS } from "../models.js";

export async function render(main, ctx) {
  const id = ctx.params[0];
  const page = h("div.page");
  main.append(page);
  let disposed = false;
  let dispose = () => {};

  // Loading skeleton
  page.append(h("div", { "aria-busy": "true", "aria-label": "Loading recording" },
    h("span.skeleton.skeleton-line.w-20"),
    h("span.skeleton.skeleton-title"),
    h("span.skeleton.skeleton-line.w-30"),
    h("div", { style: { height: "2.5rem" } }),
    ...Array.from({ length: 6 }, () => skeletonLines(["w-100", "w-85"]))));

  async function load() {
    let rec;
    try {
      rec = (await api(`/api/recordings/${id}`)).recording;
    } catch (err) {
      if (disposed) return;
      clear(page).append(backLink(), h("div.error-state", { role: "alert" },
        h("h2", err.status === 404 ? "Recording not found" : "Couldn't load this recording"),
        h("p", err.status === 404 ? "It may have been deleted, or it belongs to someone else." : err.message),
        err.status !== 404 && h("button.btn", { type: "button", onclick: load }, icon("retry"), "Retry")));
      return;
    }
    if (disposed) return;
    dispose();
    if (rec.status === "done") dispose = await renderDone(page, rec, ctx, () => disposed);
    else dispose = renderPending(page, rec, ctx, load);
  }

  await load();
  return () => { disposed = true; dispose(); };
}

function backLink() {
  return h("a.back", { href: "#/" }, icon("back"), "Library");
}

function header(rec, ctx, { onRename, edited = 0 } = {}) {
  const title = h("h1.rv-title", rec.title);
  const renameBtn = h("button.btn.btn-icon.btn-ghost", { type: "button", title: "Rename", "aria-label": "Rename recording" }, icon("pencil"));
  renameBtn.onclick = async () => {
    const input = h("input.input#rename", { value: rec.title, maxlength: 255 });
    setTimeout(() => input.select(), 30);
    const ok = await dialog({
      title: "Rename recording",
      body: h("div.field", h("label", { for: "rename" }, "Title"), input),
      actions: [{ label: "Cancel", value: false }, { label: "Save", value: true, primary: true }],
    });
    if (!ok || !input.value.trim()) return;
    try {
      const res = await api(`/api/recordings/${rec.id}`, { method: "PATCH", body: { title: input.value.trim() } });
      rec.title = res.recording.title;
      title.textContent = rec.title;
      onRename?.(rec.title);
      toast("Renamed");
    } catch (err) { toast(err.message, "error"); }
  };
  const del = h("button.btn.btn-danger.btn-sm", { type: "button" }, icon("trash"), "Delete");
  del.onclick = async () => {
    const ok = await confirmDialog("Delete this recording?", `“${rec.title}”, its audio, transcript and highlights will be permanently removed.`);
    if (!ok) return;
    try {
      await api(`/api/recordings/${rec.id}`, { method: "DELETE" });
      toast("Recording deleted");
      ctx.navigate("/");
    } catch (err) { toast(err.message, "error"); }
  };
  const meta = h("div.rv-meta",
    statusChip(rec),
    h("span", when(rec.created_at)),
    rec.duration != null && h("span.mono", humanDuration(rec.duration)),
    !rec.is_mine && h("span", `Owner: ${rec.owner}`),
    rec.model && modelChip(rec.model),
    rec.source === "browser" && h("span", "Recorded in browser"));
  const hints = vocabUsed(rec.vocabulary);
  const canRerun = rec.status === "done" || rec.status === "error";
  const rerun = canRerun && h("button.btn.btn-sm", { type: "button", onclick: () => retranscribeDialog(rec, ctx, edited) },
    icon("retry"), rec.status === "error" ? "Try another model" : "Re-transcribe");
  return { el: h("header.rv-head", backLink(), h("div.rv-title-row", title, renameBtn), meta, hints, h("div.rv-actions", rerun, del)), meta };
}

function vocabUsed(vocabulary) {
  if (!vocabulary) return null;
  const terms = vocabulary.split(", ");
  const shown = terms.slice(0, 12);
  return h("p.vocab-used", { "aria-label": `Vocabulary hints: ${vocabulary}` },
    h("span", "Hints"), shown.map((t) => h("span.vocab-term", t)),
    terms.length > shown.length && h("span", `+${terms.length - shown.length} more`));
}

function modelChip(id) {
  const el = h("span.mono", { title: "Transcription model" }, id);
  getModels().then((c) => { el.textContent = modelLabel(c, id); }).catch(() => {});
  return el;
}

async function retranscribeDialog(rec, ctx, edited) {
  let catalog;
  try {
    catalog = await getModels();
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  // Suggest the next more accurate model than the one used last time.
  const ids = catalog.models.map((m) => m.id);
  const cur = ids.indexOf(rec.model);
  const suggested = ids[Math.min(cur + 1, ids.length - 1)] || catalog.default;
  const picker = modelPicker(catalog, { name: "rerun-model", selected: cur < 0 ? catalog.default : suggested, legend: "Model" });
  const vocab = vocabField({ id: "rerun-vocab", value: rec.vocabulary || "" });
  const body = h("div.form",
    h("p.muted", "The audio is transcribed again from scratch. Highlights are kept, because they're tied to time rather than words."),
    edited > 0 && h("p.form-error", { role: "note" }, icon("alert"),
      h("span", `${edited} edited segment${edited === 1 ? "" : "s"} will be replaced by the new transcript.`)),
    picker.el,
    vocab.el);
  // Re-open with the same inputs if the hints are too long, so nothing typed is lost.
  for (;;) {
    const ok = await dialog({
      title: "Re-transcribe recording",
      body,
      actions: [{ label: "Cancel", value: false }, { label: "Re-transcribe", value: true, primary: true }],
    });
    if (!ok) return;
    if (vocab.valid) break;
    toast(`Names & terms is over ${VOCAB_MAX_CHARS} characters. Shorten it and try again.`, "error");
  }
  try {
    // Always send vocabulary: an emptied box clears the hints.
    await api(`/api/recordings/${rec.id}/retranscribe`, { method: "POST", body: { model: picker.value, vocabulary: vocab.value } });
    toast(`Queued with ${modelLabel(catalog, picker.value)}`);
    ctx.navigate(`/r/${rec.id}`);
  } catch (err) {
    toast(err.message, "error");
  }
}

// ====================================================================== pending / error

function renderPending(page, rec, ctx, reload) {
  clear(page);
  const { el: head } = header(rec, ctx);
  let timer = null;
  let card;
  if (rec.status === "error") {
    const retry = h("button.btn.btn-primary", { type: "button", dataset: { busyLabel: "Re-queuing…" } }, icon("retry"), "Retry transcription");
    retry.onclick = () => busy(retry, async () => {
      try {
        await api(`/api/recordings/${rec.id}/retry`, { method: "POST" });
        reload();
      } catch (err) { toast(err.message, "error"); }
    });
    card = h("section.error-state", { role: "alert" },
      h("p.eyebrow", "Transcription failed"),
      h("h2", "We couldn't transcribe this one"),
      h("p", rec.error || "Unknown error."),
      retry);
  } else {
    const pct = Math.round((rec.progress || 0) * 100);
    const queued = rec.status === "queued";
    card = h("section.panel.processing-card", { "aria-live": "polite" },
      h("p.eyebrow", queued ? "Waiting in queue" : "Transcribing", rec.model ? [" · ", modelChip(rec.model)] : null),
      h("p.big", queued ? "…" : `${pct}%`),
      h("div.progress", { class: queued ? "is-indeterminate" : "", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": "Transcription progress" },
        h("span", { style: { "--p": `${pct}%` } })),
      h("p", queued
        ? "Waiting for the worker. Jobs run one at a time, in upload order. The first use of a model also downloads it, which can take a few minutes."
        : "Long recordings take a while on CPU, and the more accurate models take longer. Feel free to leave. It keeps going in the background."));
    timer = setTimeout(reload, 3000);
  }
  reveal(head, card);
  page.append(head, card);
  return () => clearTimeout(timer);
}

// ====================================================================== done: player + transcript

async function renderDone(page, rec, ctx, isDisposed) {
  let segments;
  let highlights;
  try {
    [segments, highlights] = await Promise.all([
      api(`/api/recordings/${rec.id}/transcript`).then((r) => r.segments),
      api(`/api/recordings/${rec.id}/highlights`).then((r) => r.highlights),
    ]);
  } catch (err) {
    clear(page).append(backLink(), h("div.error-state", { role: "alert" },
      h("h2", "Couldn't load the transcript"), h("p", err.message),
      h("button.btn", { type: "button", onclick: () => location.reload() }, icon("retry"), "Retry")));
    return () => {};
  }
  if (isDisposed()) return () => {};
  clear(page);

  const audio = new Audio();
  audio.preload = "metadata";
  audio.src = `/api/recordings/${rec.id}/audio`;
  let duration = rec.duration || 0;
  let stopAt = null;
  let raf = 0;
  let follow = true;

  // ----------------------------------------------------------------- word index
  /** words: [{ start, end, text, el, seg }] in time order */
  let words = [];
  let starts = new Float64Array(0);
  const segEls = new Map();

  function rebuildIndex() {
    words = [];
    for (const s of segments) for (const w of s._words) words.push(w);
    starts = Float64Array.from(words, (w) => w.start);
  }

  function wordAt(t) {
    let lo = 0;
    let hi = starts.length - 1;
    let ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (ans < 0) return -1;
    return t <= words[ans].end + 1.0 ? ans : -1;
  }

  // ----------------------------------------------------------------- transcript DOM
  const transcriptEl = h("article.transcript", { "aria-label": "Transcript" });
  const segHost = h("div");

  function buildSegment(s) {
    const text = h("p.seg-text");
    s._words = [];
    const ws = s.words?.length ? s.words : [[s.start, s.end, s.text]];
    ws.forEach(([start, end, t], i) => {
      const el = h("span.w", t);
      const w = { start, end, text: t, el, seg: s };
      el._w = w;
      s._words.push(w);
      if (i) text.append(" ");
      text.append(el);
    });
    if (s.edited) text.append(h("span.seg-edited", "edited"));
    const timeBtn = h("button.seg-time", { type: "button", "aria-label": `Play from ${clock(s.start)}` }, clock(s.start, duration >= 3600));
    timeBtn.onclick = () => playFrom(s.start);
    const editBtn = h("button.btn.btn-icon.btn-sm.btn-ghost", { type: "button", title: "Edit text", "aria-label": `Edit segment at ${clock(s.start)}` }, icon("pencil"));
    editBtn.onclick = () => editSegment(s);
    const hlBtn = h("button.btn.btn-icon.btn-sm.btn-ghost", { type: "button", title: "Highlight segment", "aria-label": `Highlight segment at ${clock(s.start)}` }, icon("bookmark"));
    hlBtn.onclick = () => createHighlight(s.start, s.end, s.text);
    const el = h("div.seg", { dataset: { idx: s.idx } }, timeBtn, text, h("div.seg-tools", editBtn, hlBtn));
    s._el = el;
    segEls.set(s.id, el);
    return el;
  }

  function renderTranscript() {
    clear(segHost);
    if (!segments.length) {
      segHost.append(h("div.empty",
        h("p.empty-mark", { "aria-hidden": "true" }, "( )"),
        h("h3", "No speech detected"),
        h("p", "The transcriber didn't find any words in this audio. Check the recording has audible speech.")));
      return;
    }
    const frag = document.createDocumentFragment();
    for (const s of segments) frag.append(buildSegment(s));
    segHost.append(frag);
    rebuildIndex();
    applyHighlights();
  }

  function editSegment(s) {
    const ta = h("textarea.textarea", { value: s.text, "aria-label": `Edit text at ${clock(s.start)}`, rows: Math.min(10, Math.ceil(s.text.length / 70) + 1) });
    const err = h("div");
    const save = h("button.btn.btn-primary.btn-sm", { type: "button", dataset: { busyLabel: "Saving…" } }, icon("check"), "Save");
    const cancel = h("button.btn.btn-ghost.btn-sm", { type: "button" }, "Cancel");
    const old = s._el;
    const form = h("div.seg", h("span.seg-time", { "aria-hidden": "true" }, clock(s.start, duration >= 3600)),
      h("div.seg-edit", ta, err, h("div.seg-edit-actions", save, cancel), h("p.hint", "Word timings are kept where words still match. Ctrl+Enter saves, Esc cancels.")));
    old.replaceWith(form);
    ta.focus();
    const restore = () => { form.replaceWith(old); old.querySelector(".seg-tools button")?.focus(); };
    cancel.onclick = restore;
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); restore(); }
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save.click();
    });
    save.onclick = () => busy(save, async () => {
      err.replaceChildren();
      try {
        const res = await api(`/api/recordings/${rec.id}/segments/${s.id}`, { method: "PUT", body: { text: ta.value } });
        Object.assign(s, res.segment);
        const fresh = buildSegment(s);
        form.replaceWith(fresh);
        rebuildIndex();
        applyHighlights();
        toast("Segment saved");
        fresh.querySelector(".seg-tools button")?.focus();
      } catch (ex) {
        err.append(formError(ex.message));
      }
    });
  }

  // ----------------------------------------------------------------- highlights in transcript
  let hlMarked = [];
  function applyHighlights() {
    for (const el of hlMarked) el.classList.remove("is-hl");
    hlMarked = [];
    for (const hl of highlights) {
      for (let i = Math.max(0, wordAt(hl.start - 0.5)); i < words.length; i++) {
        const w = words[i];
        const mid = (w.start + w.end) / 2;
        if (mid > hl.end) break;
        if (mid >= hl.start) { w.el.classList.add("is-hl"); hlMarked.push(w.el); }
      }
    }
  }

  // ----------------------------------------------------------------- selection popover
  let pop = null;
  function closePop() { pop?.remove(); pop = null; }

  function selectionWords() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    if (!segHost.contains(range.commonAncestorContainer)) return null;
    const segOf = (node) => (node.nodeType === 1 ? node : node.parentElement)?.closest(".seg");
    const a = segOf(range.startContainer);
    const b = segOf(range.endContainer);
    if (!a || !b) return null;
    const ia = segments.findIndex((s) => s._el === a);
    const ib = segments.findIndex((s) => s._el === b);
    if (ia < 0 || ib < 0) return null;
    const picked = [];
    for (let i = ia; i <= ib; i++) {
      for (const w of segments[i]._words) if (range.intersectsNode(w.el)) picked.push(w);
    }
    return picked.length ? { picked, range } : null;
  }

  function maybeShowPop() {
    closePop();
    const res = selectionWords();
    if (!res) return;
    const { picked, range } = res;
    const start = picked[0].start;
    const end = picked[picked.length - 1].end;
    const text = picked.map((w) => w.text).join(" ");
    const rect = range.getBoundingClientRect();
    const host = transcriptEl.getBoundingClientRect();
    const left = Math.min(Math.max(rect.left + rect.width / 2 - host.left, 110), host.width - 110);
    pop = h("div.sel-pop", { role: "toolbar", "aria-label": "Selection actions", style: { left: `${left}px`, top: `${rect.top - host.top}px` } },
      h("button.btn.btn-primary.btn-sm", { type: "button", onclick: () => { playRange(start, end); } }, icon("play"), `Play ${humanDuration(end - start)}`),
      h("button.btn.btn-sm", { type: "button", onclick: () => { closePop(); createHighlight(start, end, text); } }, icon("bookmark"), "Highlight"),
      h("button.btn.btn-sm.btn-ghost.btn-icon", { type: "button", title: "Copy text", "aria-label": "Copy text", onclick: () => copyText(text) }, icon("copy")));
    pop.addEventListener("mousedown", (e) => e.preventDefault()); // keep the selection
    transcriptEl.append(pop);
  }

  segHost.addEventListener("mouseup", () => setTimeout(maybeShowPop, 0));
  segHost.addEventListener("touchend", () => setTimeout(maybeShowPop, 250));
  segHost.addEventListener("keyup", (e) => e.shiftKey && maybeShowPop());
  const onSelChange = () => { if (pop && window.getSelection()?.isCollapsed) closePop(); };
  document.addEventListener("selectionchange", onSelChange);

  segHost.addEventListener("click", (e) => {
    const el = e.target.closest(".w");
    if (!el || !window.getSelection()?.isCollapsed) return;
    playFrom(el._w.start);
  });

  // ----------------------------------------------------------------- player
  const playBtn = h("button.play-btn", { type: "button", "aria-label": "Play" }, icon("play"));
  const timeNow = h("b", "0:00");
  const timeTotal = h("span", clock(duration));
  const scrub = h("input.scrub", { type: "range", min: 0, max: duration || 0, step: 0.1, value: 0, "aria-label": "Seek" });
  const speed = h("select.select", { "aria-label": "Playback speed" },
    [0.75, 1, 1.25, 1.5, 1.75, 2].map((r) => h("option", { value: r, selected: r === 1 }, `${r}×`)));
  const back = h("button.btn.btn-icon.btn-ghost", { type: "button", title: "Back 5 seconds", "aria-label": "Back 5 seconds" }, icon("back5"));
  const fwd = h("button.btn.btn-icon.btn-ghost", { type: "button", title: "Forward 5 seconds", "aria-label": "Forward 5 seconds" }, icon("fwd5"));
  const audioErr = h("div");
  const player = h("section.panel.player.player-dock", { "aria-label": "Audio player" },
    h("div.player-main", playBtn, h("div", { style: { flex: "1", "min-width": "0" } },
      h("p.player-time", timeNow, " / ", timeTotal), scrub)),
    h("div.player-row", h("div", { style: { display: "flex", gap: ".25rem" } }, back, fwd), speed),
    audioErr,
    h("p.keys", h("span", h("kbd", "Space"), " play/pause"), h("span", h("kbd", "←"), h("kbd", "→"), " 5s"), h("span", "Select text to play it")));
  document.body.classList.add("has-dock");

  playBtn.onclick = () => { stopAt = null; audio.paused ? audio.play().catch(playError) : audio.pause(); };
  back.onclick = () => seek(audio.currentTime - 5);
  fwd.onclick = () => seek(audio.currentTime + 5);
  speed.onchange = () => { audio.playbackRate = Number(speed.value); };
  scrub.addEventListener("input", () => { stopAt = null; seek(Number(scrub.value)); });

  function playError(err) {
    if (err?.name === "AbortError") return;
    toast("Playback was blocked or failed. Try pressing play again.", "error");
  }
  audio.addEventListener("error", () => {
    audioErr.replaceChildren(formError("The audio couldn't be loaded. The media file may be missing."));
  });
  audio.addEventListener("loadedmetadata", () => {
    if (Number.isFinite(audio.duration)) {
      duration = audio.duration;
      scrub.max = duration;
      timeTotal.textContent = clock(duration);
    }
  });
  audio.addEventListener("play", () => { playBtn.replaceChildren(icon("pause")); playBtn.setAttribute("aria-label", "Pause"); loop(); });
  audio.addEventListener("pause", () => { playBtn.replaceChildren(icon("play")); playBtn.setAttribute("aria-label", "Play"); cancelAnimationFrame(raf); tick(); });
  audio.addEventListener("seeked", tick);

  function seek(t) {
    audio.currentTime = Math.max(0, Math.min(duration || Infinity, t));
    tick();
  }
  function playFrom(t) {
    stopAt = null;
    seek(t);
    audio.play().catch(playError);
  }
  function playRange(start, end) {
    seek(start);
    stopAt = end;
    audio.play().catch(playError);
  }

  let currentIdx = -1;
  let currentSeg = null;
  function tick() {
    const t = audio.currentTime;
    if (stopAt != null && t >= stopAt) { audio.pause(); stopAt = null; }
    timeNow.textContent = clock(t, duration >= 3600);
    if (document.activeElement !== scrub) scrub.value = t;
    scrub.style.setProperty("--p", `${duration ? (t / duration) * 100 : 0}%`);
    const idx = wordAt(t);
    if (idx !== currentIdx) {
      if (currentIdx >= 0) words[currentIdx]?.el.classList.remove("is-current");
      currentIdx = idx;
      if (idx >= 0) {
        const w = words[idx];
        w.el.classList.add("is-current");
        if (w.seg !== currentSeg) {
          currentSeg?._el?.classList.remove("is-active");
          currentSeg = w.seg;
          currentSeg._el?.classList.add("is-active");
          if (follow && !audio.paused) scrollIntoViewIfNeeded(currentSeg._el);
        }
      }
    }
  }
  function loop() {
    tick();
    if (!audio.paused) raf = requestAnimationFrame(loop);
  }
  function scrollIntoViewIfNeeded(el) {
    if (!el) return;
    const r = el.getBoundingClientRect();
    const dockH = window.innerWidth < 1024 ? player.offsetHeight : 0;
    if (r.top < 80 || r.bottom > window.innerHeight - dockH - 20) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // ----------------------------------------------------------------- highlights panel
  const hlCount = h("span.count");
  const hlList = h("ul.hl-list");

  function renderHighlights() {
    hlCount.textContent = String(highlights.length);
    clear(hlList);
    if (!highlights.length) {
      hlList.append(h("li.empty", { style: { padding: "1.25rem" } },
        h("p", "Select words in the transcript and choose ", h("strong", "Highlight"), " to save a moment with a note.")));
      return;
    }
    for (const hl of highlights) {
      const editBtn = h("button.btn.btn-icon.btn-sm.btn-ghost", { type: "button", title: "Edit note", "aria-label": "Edit note" }, icon("pencil"));
      const delBtn = h("button.btn.btn-icon.btn-sm.btn-ghost", { type: "button", title: "Delete highlight", "aria-label": "Delete highlight" }, icon("trash"));
      const clip = h("a.btn.btn-icon.btn-sm.btn-ghost", { href: `/api/highlights/${hl.id}/clip`, download: "", title: "Download audio clip", "aria-label": "Download audio clip" }, icon("download"));
      editBtn.onclick = () => editNote(hl);
      delBtn.onclick = async () => {
        if (!(await confirmDialog("Delete highlight?", "The note will be removed. The transcript is unchanged."))) return;
        try {
          await api(`/api/highlights/${hl.id}`, { method: "DELETE" });
          highlights = highlights.filter((x) => x.id !== hl.id);
          renderHighlights();
          applyHighlights();
        } catch (err) { toast(err.message, "error"); }
      };
      hlList.append(h("li.hl",
        h("button.hl-quote", { type: "button", onclick: () => playRange(hl.start, hl.end), "aria-label": `Play highlight at ${clock(hl.start)}: ${hl.text}` },
          h("span.clamp", `“${hl.text}”`)),
        hl.note && h("p.hl-note", hl.note),
        h("div.hl-foot", h("span.mono", `${clock(hl.start)}–${clock(hl.end)}`), h("div.hl-tools", editBtn, clip, delBtn))));
    }
  }

  async function noteDialog(title, initial, quote) {
    const ta = h("textarea.textarea#hl-note", { value: initial, maxlength: 5000, placeholder: "Why does this matter? (optional)" });
    setTimeout(() => ta.focus(), 30);
    const ok = await dialog({
      title,
      body: h("div.form", h("blockquote.muted", { style: { margin: 0, "padding-left": "1rem", "border-left": "3px solid var(--accent)" } }, `“${quote.length > 280 ? `${quote.slice(0, 280)}…` : quote}”`),
        h("div.field", h("label", { for: "hl-note" }, "Note"), ta)),
      actions: [{ label: "Cancel", value: false }, { label: "Save highlight", value: true, primary: true }],
    });
    return ok ? ta.value : null;
  }

  async function createHighlight(start, end, text) {
    const note = await noteDialog("Save highlight", "", text);
    if (note == null) return;
    try {
      const res = await api(`/api/recordings/${rec.id}/highlights`, { method: "POST", body: { start, end, text, note } });
      highlights = [...highlights, res.highlight].sort((a, b) => a.start - b.start);
      renderHighlights();
      applyHighlights();
      window.getSelection()?.removeAllRanges();
      toast("Highlight saved");
    } catch (err) { toast(err.message, "error"); }
  }

  async function editNote(hl) {
    const note = await noteDialog("Edit note", hl.note, hl.text);
    if (note == null) return;
    try {
      const res = await api(`/api/highlights/${hl.id}`, { method: "PATCH", body: { note } });
      Object.assign(hl, res.highlight);
      renderHighlights();
    } catch (err) { toast(err.message, "error"); }
  }

  // ----------------------------------------------------------------- layout
  const { el: head } = header(rec, ctx, { edited: segments.filter((x) => x.edited).length });
  const followToggle = h("label.toggle", h("input", { type: "checkbox", checked: true, onchange: (e) => { follow = e.target.checked; } }), "Follow along");
  const words_count = segments.reduce((n, s) => n + (s.words?.length || 0), 0);
  transcriptEl.append(
    h("div.transcript-toolbar", h("span.eyebrow", `${segments.length} segments · ${words_count.toLocaleString()} words`), followToggle),
    segHost);

  const exportSection = h("section.side-section", { "aria-labelledby": "exp-h" },
    h("h3#exp-h", "Export"),
    h("div.export-row", ["txt", "srt", "vtt"].map((f) =>
      h("a.btn.btn-sm", { href: `/api/recordings/${rec.id}/export?format=${f}`, download: "" }, icon("download"), f.toUpperCase()))));
  const hlSection = h("section.side-section", { "aria-labelledby": "hl-h" }, h("h3#hl-h", "Highlights", hlCount), hlList);
  const side = h("aside.rv-side", { "aria-label": "Player and highlights" }, player, h("div", { style: { padding: "0 .25rem" } }, exportSection, hlSection));
  const layout = h("div.rv-layout", h("section", transcriptEl), side);
  reveal(head, transcriptEl, player, exportSection, hlSection);
  page.append(head, layout);
  renderTranscript();
  renderHighlights();

  // ?t= deep link (from search)
  const t0 = Number(ctx.query.get("t"));
  if (Number.isFinite(t0) && t0 > 0) {
    seek(t0);
    const idx = wordAt(t0 + 0.01);
    const segEl = idx >= 0 ? words[idx].seg._el : segments.find((s) => s.start >= t0 - 0.01)?._el;
    setTimeout(() => segEl?.scrollIntoView({ block: "center" }), 80);
  }

  // ----------------------------------------------------------------- keyboard
  const onKey = (e) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = e.target.tagName;
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.target.isContentEditable;
    if (document.querySelector("dialog[open]")) return;
    if (e.key === "Escape") { closePop(); window.getSelection()?.removeAllRanges(); return; }
    if (typing) return;
    if (e.key === " " && tag !== "BUTTON" && tag !== "A") { e.preventDefault(); playBtn.click(); }
    else if (e.key === "ArrowLeft" && !e.shiftKey) { e.preventDefault(); seek(audio.currentTime - 5); }
    else if (e.key === "ArrowRight" && !e.shiftKey) { e.preventDefault(); seek(audio.currentTime + 5); }
  };
  document.addEventListener("keydown", onKey);

  return () => {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    cancelAnimationFrame(raf);
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("selectionchange", onSelChange);
    document.body.classList.remove("has-dock");
  };
}
