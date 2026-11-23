// Fetch wrapper: JSON in/out, CSRF header, consistent errors.

export class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

const state = { csrf: null, onAuthLost: () => {}, onPasswordRequired: () => {} };

export function configure({ onAuthLost, onPasswordRequired }) {
  state.onAuthLost = onAuthLost;
  state.onPasswordRequired = onPasswordRequired;
}

export function setCsrf(token) {
  state.csrf = token;
}

function detailMessage(data, status) {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) {
    const field = d[0].loc?.slice(-1)[0];
    return field ? `${field}: ${d[0].msg}` : d[0].msg;
  }
  if (status === 413) return "That file is too large.";
  if (status >= 500) return "The server hit a problem. Please try again.";
  return `Request failed (${status}).`;
}

export async function api(path, { method = "GET", body, headers = {}, raw = false, signal } = {}) {
  const opts = { method, headers: { ...headers }, credentials: "same-origin", signal };
  if (method !== "GET" && state.csrf) opts.headers["X-CSRF-Token"] = state.csrf;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, "Can't reach the server. Check your connection.");
  }
  if (raw && res.ok) return res;
  const data = res.headers.get("content-type")?.includes("json") ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    const msg = detailMessage(data, res.status);
    if (res.status === 401 && !path.startsWith("/api/auth/login")) state.onAuthLost();
    if (res.status === 403 && msg === "password_change_required") state.onPasswordRequired();
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

/** Upload a Blob/File as the raw request body, with progress (fetch can't report upload progress). */
export function uploadFile(file, { filename, title, source = "upload", model, vocabulary, onProgress } = {}) {
  const params = new URLSearchParams({ filename: filename || file.name, source });
  if (title) params.set("title", title);
  if (model) params.set("model", model);
  if (vocabulary) params.set("vocabulary", vocabulary);
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open("POST", `/api/recordings?${params}`);
    xhr.withCredentials = true;
    if (state.csrf) xhr.setRequestHeader("X-CSRF-Token", state.csrf);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onerror = () => reject(new ApiError(0, "Upload failed: connection lost."));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled."));
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data.recording);
      else {
        if (xhr.status === 401) state.onAuthLost();
        reject(new ApiError(xhr.status, detailMessage(data, xhr.status), data));
      }
    };
    xhr.send(file);
  });
  promise.abort = () => xhr.abort();
  return promise;
}
