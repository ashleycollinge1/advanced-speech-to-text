import { api, uploadFile } from "../api.js";
import { h, append, clear, icon, reveal, humanDuration, when, bytes, toast, clock, skeletonLines, formError } from "../dom.js";
import { Recorder, recordingSupported } from "../recorder.js";
import { getModels, modelPicker, preferredModel, rememberModel, vocabField, VOCAB_MAX_CHARS } from "../models.js";

const ACCEPT = "audio/*,video/*,.mp3,.m4a,.wav,.flac,.ogg,.opus,.webm,.aac,.wma,.amr,.aiff,.caf,.mkv,.mov,.mp4,.3gp";

export function statusChip(rec) {
  const label = {
    queued: "Queued",
    processing: `${Math.round((rec.progress || 0) * 100)}%`,
    done: "Ready",
    error: "Failed",
  }[rec.status] || rec.status;
  return h(`span.chip.chip-${rec.status}`, label);
}

export function render(main, ctx) {
  const isAdmin = ctx.session.user.role === "admin";
  let scope = ctx.query.get("scope") === "all" && isAdmin ? "all" : "mine";
  let recordings = null;
  let pollTimer = null;
  let disposed = false;
  const activeUploads = new Set();
  // null = still loading the model list; false = no model downloaded yet.
  let modelsReady = null;
  let picker = null;
  let dropTitle = null;

  // ------------------------------------------------------------------ header
  const statsEl = h("div.stats");
  const head = h("header.lib-head.page-head",
    h("div", h("p.eyebrow", "Library"), h("h1", h("span.thin", "Your "), "recordings")),
    statsEl);

  // ------------------------------------------------------------------ upload tile
  const fileInput = h("input", { type: "file", accept: ACCEPT, multiple: true, id: "file-input", "aria-label": "Choose audio files" });
  const drop = h("label.drop", { for: "file-input" },
    fileInput,
    h("div", h("p.eyebrow", "Upload"), h("div", { style: { height: "0.75rem" } }),
      h("h2", { ref: (el) => { dropTitle = el; } }, "Drop audio here", h("span.thin.accent", ".")),
      h("p.muted", { style: { "margin-top": "0.75rem", "max-width": "44ch" } },
        "MP3, M4A, WAV, FLAC, OGG, WebM, or video files. Long meetings and interviews are fine.")),
    h("div.drop-actions",
      h("span.btn.btn-primary", { "aria-hidden": "true" }, icon("upload"), "Choose files"),
      h("span.faint", "or drag & drop")));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
  drop.addEventListener("drop", (e) => startUploads([...e.dataTransfer.files]));
  // While no model is downloaded the label mustn't open the file chooser.
  drop.addEventListener("click", (e) => { if (modelsReady === false) { e.preventDefault(); startUploads([]); } });
  fileInput.addEventListener("change", () => { startUploads([...fileInput.files]); fileInput.value = ""; });

  // ------------------------------------------------------------------ record tile
  const recPanel = h("section.panel.panel-pad.rec-panel", { "aria-labelledby": "rec-h" });
  let recorder = null;
  renderRecIdle();

  function renderRecIdle(errorMsg) {
    clear(recPanel);
    const supported = recordingSupported() && window.isSecureContext;
    const blocked = modelsReady === false;
    append(recPanel,
      h("div", h("p.eyebrow", "Record"), h("div", { style: { height: "0.75rem" } }), h("h2#rec-h", "Capture it live")),
      h("p.muted", supported
        ? "Record from your microphone. It'll be transcribed as soon as you save."
        : "Browser recording needs HTTPS (or localhost) and a modern browser."),
      errorMsg && formError(errorMsg),
      blocked && h("p.hint", "Download a transcription model below to enable recording."),
      h("div.rec-actions",
        h("button.btn.rec-btn", { type: "button", disabled: !supported || blocked, onclick: startRecording },
          h("span.rec-dot", { "aria-hidden": "true" }), "Start recording")));
  }

  async function startRecording() {
    const timer = h("p.rec-timer", { "aria-live": "off" }, "0:00");
    const canvas = h("canvas.rec-meter", { "aria-hidden": "true" });
    const pauseBtn = h("button.btn", { type: "button" }, icon("pause"), "Pause");
    const stopBtn = h("button.btn.btn-primary", { type: "button" }, icon("stop"), "Stop");
    const cancelBtn = h("button.btn.btn-ghost", { type: "button" }, "Discard");
    recorder = new Recorder({ canvas, onTick: (t) => { timer.textContent = clock(t); } });
    try {
      await recorder.start();
    } catch (err) {
      recorder = null;
      renderRecIdle(err.name === "NotAllowedError" ? "Microphone access was blocked. Allow it in your browser settings." : `Couldn't start recording: ${err.message}`);
      return;
    }
    clear(recPanel).classList.add("is-recording");
    append(recPanel,
      h("div", h("p.eyebrow", h("span.rec-dot", { style: { display: "inline-block", width: ".55rem", height: ".55rem", "border-radius": "50%", background: "var(--accent)", "margin-right": ".5rem" } }), "Recording"), timer),
      canvas,
      h("div.rec-actions", pauseBtn, stopBtn, cancelBtn));
    pauseBtn.onclick = () => {
      if (recorder.paused) { recorder.resume(); pauseBtn.replaceChildren(icon("pause"), "Pause"); }
      else { recorder.pause(); pauseBtn.replaceChildren(icon("play"), "Resume"); }
    };
    cancelBtn.onclick = () => { recorder.cancel(); recorder = null; recPanel.classList.remove("is-recording"); renderRecIdle(); };
    stopBtn.onclick = async () => {
      const result = await recorder.stop();
      recorder = null;
      recPanel.classList.remove("is-recording");
      if (!result || result.blob.size === 0) return renderRecIdle("Nothing was recorded.");
      renderRecSave(result);
    };
  }

  function renderRecSave({ blob, ext, duration }) {
    clear(recPanel);
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    const title = h("input.input#rec-title", { value: `Recording ${stamp}`, maxlength: 255 });
    const save = h("button.btn.btn-primary", { type: "button" }, icon("upload"), "Save & transcribe");
    const discard = h("button.btn.btn-ghost", { type: "button" }, "Discard");
    append(recPanel,
      h("div", h("p.eyebrow", `Recorded ${humanDuration(duration)}`), h("div", { style: { height: ".75rem" } }), h("h2#rec-h", "Keep this take?")),
      h("audio", { controls: true, src: url, style: { width: "100%" } }),
      h("div.field", h("label", { for: "rec-title" }, "Title"), title),
      h("div.rec-actions", save, discard));
    discard.onclick = () => { URL.revokeObjectURL(url); renderRecIdle(); };
    save.onclick = () => {
      // Validate first so an invalid hint list never discards the take.
      if (!vocab.valid) {
        toast(`Names & terms is over ${VOCAB_MAX_CHARS} characters. Shorten it, then save.`, "error");
        document.getElementById("upload-vocab")?.focus();
        return;
      }
      const file = new File([blob], `recording.${ext}`, { type: blob.type });
      URL.revokeObjectURL(url);
      renderRecIdle();
      startUploads([file], { title: title.value.trim(), source: "browser" });
    };
  }

  // ------------------------------------------------------------------ model picker (applies to uploads + recordings)
  let chosenModel = null; // a downloaded model, or null if none yet
  const pickerSlot = h("div", h("span.skeleton.skeleton-line.w-20"), h("span.skeleton.skeleton-line.w-70"));
  // Hints apply to every upload/recording made from this page until cleared.
  const vocab = vocabField({ id: "upload-vocab" });
  const pickerHost = h("section.panel.panel-pad.picker-panel", { "aria-label": "Transcription settings" }, pickerSlot, vocab.el);
  function setModelsReady(ready) {
    const changed = modelsReady !== ready;
    modelsReady = ready;
    drop.classList.toggle("is-disabled", !ready);
    fileInput.disabled = !ready;
    dropTitle.replaceChildren(ready ? "Drop audio here" : "Download a model first", h("span.thin.accent", "."));
    if (changed && !recorder && !recPanel.querySelector("audio")) renderRecIdle();
  }

  getModels({ fresh: true }).then((catalog) => {
    if (disposed) return;
    picker = modelPicker(catalog, {
      name: "upload-model",
      selected: preferredModel(catalog),
      legend: "Transcription quality for new recordings",
      onChange: (id) => { chosenModel = id; if (id) rememberModel(id); },
      onReadyChange: setModelsReady,
    });
    chosenModel = picker.value;
    pickerSlot.replaceChildren(picker.el);
  }).catch(() => {
    pickerSlot.replaceChildren(h("p.hint", "Couldn't load the model list. Reload the page to try again."));
  });

  const bento = h("div.bento", drop, recPanel, pickerHost);

  // ------------------------------------------------------------------ uploads in flight
  const uploadsEl = h("ul.uploads", { "aria-label": "Uploads in progress" });

  function startUploads(files, opts = {}) {
    if (!chosenModel) {
      toast("Download a transcription model first. See “Transcription quality” below.", "error");
      pickerHost.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (!vocab.valid) {
      toast(`Names & terms is over ${VOCAB_MAX_CHARS} characters. Shorten it before uploading.`, "error");
      document.getElementById("upload-vocab")?.focus();
      return;
    }
    const vocabulary = vocab.value;
    for (const file of files) {
      const bar = h("span");
      const pct = h("span.mono.faint", "0%");
      const cancel = h("button.btn.btn-sm.btn-ghost", { type: "button" }, "Cancel");
      const item = h("li.upload-item",
        h("div", h("strong", opts.title || file.name), " ", h("span.faint", bytes(file.size))),
        h("div", { style: { display: "flex", gap: ".5rem", "align-items": "center" } }, pct, cancel),
        h("div.progress", { role: "progressbar", "aria-label": `Uploading ${file.name}` }, bar));
      uploadsEl.prepend(item);
      const up = uploadFile(file, {
        title: opts.title,
        source: opts.source || "upload",
        model: chosenModel,
        vocabulary,
        onProgress: (p) => {
          bar.style.setProperty("--p", `${(p * 100).toFixed(1)}%`);
          pct.textContent = `${Math.round(p * 100)}%`;
        },
      });
      activeUploads.add(up);
      cancel.onclick = () => up.abort();
      up.then((rec) => {
        item.remove();
        toast(`“${rec.title}” uploaded. Transcription queued.`);
        if (recordings) { recordings.unshift(rec); renderList(); schedulePoll(); }
      }).catch((err) => {
        item.classList.add("is-error");
        pct.textContent = "";
        cancel.replaceWith(h("button.btn.btn-sm.btn-ghost", { type: "button", onclick: () => item.remove() }, "Dismiss"));
        item.append(h("p.hint", { style: { color: "var(--danger)", "grid-column": "1 / -1" } }, err.message));
      }).finally(() => activeUploads.delete(up));
    }
  }

  // ------------------------------------------------------------------ list
  const scopeToggle = isAdmin && h("div.segmented", { role: "group", "aria-label": "Show recordings" },
    ["mine", "all"].map((s) => h("button", {
      type: "button",
      "aria-pressed": String(scope === s),
      onclick: () => { scope = s; ctx.navigate(s === "all" ? "/?scope=all" : "/"); },
    }, s === "mine" ? "Mine" : "Everyone")));
  const listHost = h("div");
  const listSection = h("section", { "aria-labelledby": "list-h" },
    h("div.list-head", h("h2#list-h", "Recent"), scopeToggle),
    listHost);

  function renderStats() {
    clear(statsEl);
    if (!recordings) return statsEl.append(h("span.skeleton.skeleton-line.w-100", { style: { width: "12rem", height: "2rem" } }));
    const total = recordings.reduce((s, r) => s + (r.duration || 0), 0);
    const pending = recordings.filter((r) => r.status === "queued" || r.status === "processing").length;
    const stat = (n, label) => h("div.stat", h("b", n), h("span.eyebrow", label));
    append(statsEl, stat(String(recordings.length), "recordings"), stat(humanDuration(total), "audio"), pending ? stat(String(pending), "in progress") : null);
  }

  function renderList() {
    renderStats();
    clear(listHost);
    if (!recordings) {
      listHost.append(h("div", { "aria-busy": "true", "aria-label": "Loading recordings" },
        [0, 1, 2, 3].map(() => h("div.skeleton-row", skeletonLines(["w-40", "w-20"])))));
      return;
    }
    if (!recordings.length) {
      listHost.append(h("div.empty",
        h("p.empty-mark", { "aria-hidden": "true" }, "— —"),
        h("h3", scope === "all" ? "No recordings from anyone yet" : "Nothing here yet"),
        h("p", "Upload a file or record something above. The transcript shows up here once it's ready.")));
      return;
    }
    listHost.append(h("ol.rec-list", recordings.map((r) => {
      const busy = r.status === "processing" || r.status === "queued";
      return h("li.rec-row", h("a.rec-link", { href: `#/r/${r.id}` },
        h("span.rec-title", r.title),
        h("span.rec-meta",
          h("span.m-date", when(r.created_at)),
          h("span.m-dur.mono", humanDuration(r.duration)),
          !r.is_mine && h("span.m-owner", `by ${r.owner}`)),
        h("span.rec-status", statusChip(r), !r.is_mine && scope === "all" ? h("span.faint", { style: { "margin-left": ".5rem", "font-size": ".8rem" } }, r.owner) : null),
        busy && h("div.progress", {
          class: r.status === "queued" ? "is-indeterminate" : "",
          role: "progressbar",
          "aria-label": `Transcribing ${r.title}`,
          "aria-valuenow": Math.round((r.progress || 0) * 100),
        }, h("span", { style: { "--p": `${(r.progress || 0) * 100}%` } }))));
    })));
  }

  async function load() {
    try {
      const res = await api(`/api/recordings?scope=${scope}`);
      if (disposed) return;
      recordings = res.recordings;
      renderList();
      schedulePoll();
    } catch (err) {
      if (disposed) return;
      clear(listHost).append(h("div.error-state", { role: "alert" },
        h("h2", "Couldn't load recordings"), h("p", err.message),
        h("button.btn", { type: "button", onclick: () => { recordings = null; renderList(); load(); } }, icon("retry"), "Retry")));
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (disposed || !recordings?.some((r) => r.status === "queued" || r.status === "processing")) return;
    pollTimer = setTimeout(async () => {
      try {
        const res = await api(`/api/recordings?scope=${scope}`);
        if (disposed) return;
        const before = new Map(recordings.map((r) => [r.id, r.status]));
        recordings = res.recordings;
        for (const r of recordings) {
          if (before.get(r.id) === "processing" && r.status === "done") toast(`“${r.title}” is ready.`);
          if (before.get(r.id) !== "error" && r.status === "error") toast(`“${r.title}” failed to transcribe.`, "error");
        }
        renderList();
      } catch { /* transient; try again */ }
      schedulePoll();
    }, 4000);
  }

  // ------------------------------------------------------------------ mount
  const page = h("div.page", head, bento, uploadsEl, listSection);
  reveal(head, drop, recPanel, pickerHost, listSection);
  main.append(page);
  renderList();
  load();

  const beforeUnload = (e) => {
    if (activeUploads.size || recorder) { e.preventDefault(); e.returnValue = ""; }
  };
  window.addEventListener("beforeunload", beforeUnload);

  return () => {
    disposed = true;
    clearTimeout(pollTimer);
    window.removeEventListener("beforeunload", beforeUnload);
    recorder?.cancel();
    picker?.destroy();
    // Uploads continue in the background; their results are toasted when done.
  };
}
