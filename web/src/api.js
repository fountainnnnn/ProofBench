import { safeVisibleText, sanitizeForDisplay } from "./displaySafety.js";

const configuredBase = String(import.meta.env.VITE_API_BASE_URL || "").trim();
const BASE = configuredBase === "/"
  ? ""
  : configuredBase.replace(/\/+$/, "");
export const AUTH_CHANGE_EVENT = "proofbench-auth-change";
// The only execution mode this client can request. It is a module constant
// rather than component state so no UI path can make a write non-real.
export const RUN_MODE = "real";
let localMode = false;
// Deliberately module memory only. A reload discards write access while the
// HttpOnly cookie can continue authorising read-only browser transports.
let authToken = "";

export function isLocalMode() {
  return localMode;
}

export function getAuthToken() {
  return authToken;
}

function emitAuthChange(context) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(AUTH_CHANGE_EVENT, { detail: context }));
  }
}

export async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (authToken && !headers.has("Authorization") && !headers.has("X-API-Key")) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }
  const response = await fetch(url, { ...options, headers, credentials: "include" });
  if (response.status === 401) {
    authToken = "";
    localMode = false;
    emitAuthChange({ authMode: "authenticated", cookieAuthenticated: false, writeAuthenticated: false });
  }
  return response;
}

function publicErrorDetail(value) {
  const sanitized = sanitizeForDisplay(value);
  const text = typeof sanitized === "string" ? sanitized : JSON.stringify(sanitized);
  return safeVisibleText(text).slice(0, 500);
}

async function jsonOrThrow(res, genericMessage = "") {
  if (!res.ok) {
    if (genericMessage) throw new Error(genericMessage);
    const body = await res.json().catch(() => null);
    const detail = body?.detail || body?.error || body?.message || res.statusText;
    throw new Error(`${res.status}: ${publicErrorDetail(detail)}`);
  }
  return res.json();
}

function authContext(body) {
  const mode = body?.auth_mode;
  if (mode !== "local" && mode !== "authenticated") {
    throw new Error("The server returned an invalid authentication profile.");
  }
  localMode = mode === "local";
  const context = {
    authMode: mode,
    localMode,
    cookieAuthenticated: Boolean(body?.cookie_authenticated),
    writeAuthenticated: localMode || (Boolean(body?.write_authenticated) && Boolean(authToken)),
  };
  emitAuthChange(context);
  return context;
}

// Credential-free profile probe. In authenticated mode an HttpOnly cookie can
// survive reload, but it never restores the in-memory bearer needed to write.
export async function bootstrapAuthSession() {
  const response = await fetch(`${BASE}/api/auth/session`, { credentials: "include" });
  if (!response.ok) {
    authToken = "";
    localMode = false;
    throw new Error("ProofBench could not verify this deployment's authentication profile.");
  }
  return authContext(await jsonOrThrow(response));
}

export async function createAuthSession(token) {
  const candidate = String(token || "").trim();
  if (!candidate) throw new Error("Enter the password.");
  const response = await fetch(`${BASE}/api/auth/session`, {
    method: "POST",
    headers: { Authorization: `Bearer ${candidate}` },
    credentials: "include",
  });
  await jsonOrThrow(response);
  authToken = candidate;
  localMode = false;
  const context = {
    authMode: "authenticated", localMode: false,
    cookieAuthenticated: true, writeAuthenticated: true,
  };
  emitAuthChange(context);
  return context;
}

export async function logoutAuthSession() {
  try {
    const response = await fetch(`${BASE}/api/auth/session`, {
      method: "DELETE", credentials: "include",
    });
    await jsonOrThrow(response);
  } finally {
    authToken = "";
    localMode = false;
    emitAuthChange({
      authMode: "authenticated", localMode: false,
      cookieAuthenticated: false, writeAuthenticated: false,
    });
  }
}

let authRefreshPromise = null;

export async function ensureAuthSession() {
  if (!authRefreshPromise) {
    authRefreshPromise = (authToken
      ? createAuthSession(authToken)
      : bootstrapAuthSession().then((context) => {
          if (!context.localMode) throw new Error("Re-enter your password to continue.");
          return context;
        }))
      .finally(() => { authRefreshPromise = null; });
  }
  return authRefreshPromise;
}

// ProofBench executes real benchmarks only. `mode` is not a caller argument:
// the client has no way to request anything else, and the server rejects an
// explicit "demo" with 422 before it mutates a session or run.
export async function postChat(message, sessionId, datasetId) {
  const res = await apiFetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId || undefined,
      dataset_id: datasetId || undefined,
      mode: RUN_MODE,
    }),
  });
  return jsonOrThrow(res);
}

export async function uploadDataset({ images, groundTruth, useSynthetic }) {
  if (useSynthetic) {
    const res = await apiFetch(`${BASE}/api/datasets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_synthetic: true }),
    });
    return jsonOrThrow(res);
  }
  const form = new FormData();
  (images || []).forEach((f) => form.append("images", f));
  if (groundTruth) form.append("ground_truth", groundTruth);
  const res = await apiFetch(`${BASE}/api/datasets`, { method: "POST", body: form });
  return jsonOrThrow(res);
}

export async function generateDataset(prompt, n = 12) {
  const res = await apiFetch(`${BASE}/api/datasets/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, n }),
  });
  return jsonOrThrow(res, "The dataset designer could not produce a dataset.");
}

export async function getDatasetPreview(datasetId) {
  const res = await apiFetch(`${BASE}/api/datasets/${encodeURIComponent(datasetId)}/preview`);
  return jsonOrThrow(res);
}

export function datasetImageUrl(datasetId, docId) {
  return `${BASE}/api/datasets/${encodeURIComponent(datasetId)}/images/${encodeURIComponent(docId)}`;
}

export async function listDatasets() {
  const res = await apiFetch(`${BASE}/api/datasets`);
  const body = await jsonOrThrow(res);
  return Array.isArray(body) ? body : (body.datasets || []);
}

export async function deleteDataset(datasetId) {
  const res = await apiFetch(`${BASE}/api/datasets/${encodeURIComponent(datasetId)}`, {
    method: "DELETE",
  });
  return jsonOrThrow(res);
}

export async function startRun(sessionId, spec) {
  const res = await apiFetch(`${BASE}/api/sessions/${encodeURIComponent(sessionId)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spec, mode: RUN_MODE }),
  });
  return jsonOrThrow(res);
}

export async function stopRun(sessionId) {
  const res = await apiFetch(`${BASE}/api/sessions/${encodeURIComponent(sessionId)}/stop`, { method: "POST" });
  return jsonOrThrow(res);
}

export async function openEvents(sessionId) {
  await ensureAuthSession();
  return new EventSource(`${BASE}/api/sessions/${encodeURIComponent(sessionId)}/events`, {
    withCredentials: true,
  });
}

export async function listSessions() {
  const res = await apiFetch(`${BASE}/api/sessions`);
  const sessions = await jsonOrThrow(res);
  if (!Array.isArray(sessions)) return sessions;
  /* Newest first. The API returns sessions oldest-first, so a freshly created
     one landed at the BOTTOM of a long history — below the fold in the rail,
     which read as "new benchmarks never appear in the list". Sorted here rather
     than in each list so every surface agrees on the order. */
  return [...sessions].sort(
    (a, b) => new Date(b?.created_at || 0) - new Date(a?.created_at || 0),
  );
}

export async function getScraperOrder() {
  return jsonOrThrow(await apiFetch(`${BASE}/api/settings/scrapers`));
}

export async function saveScraperOrder(order) {
  return jsonOrThrow(await apiFetch(`${BASE}/api/settings/scrapers`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order }),
  }));
}

/* One turn searches official documentation, proposes a connector, and validates
   it before answering. The answer itself arrives whole, but the research in
   front of it takes long enough to need narrating, so the stream carries the
   steps and then the finished turn. The status call is a configuration check,
   so opening Settings never starts an agent turn. */
export async function getIntegrationAgentStatus() {
  const res = await apiFetch(`${BASE}/api/settings/integration-agent`);
  return jsonOrThrow(res, "Could not load the integration agent status.");
}

export async function sendIntegrationAgentMessage(message, history = []) {
  const res = await apiFetch(`${BASE}/api/settings/integration-agent/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  });
  return jsonOrThrow(res, "The integration agent could not answer that.");
}

/* EventSource cannot POST, so the stream is read off the fetch body directly.
   `onProgress` is called for each research step; the promise resolves with the
   finished turn. Falls back to the plain endpoint wherever streaming is not
   available (no ReadableStream, or a proxy that buffered the response). */
export async function streamIntegrationAgentMessage(message, history = [], onProgress) {
  let res;
  try {
    res = await apiFetch(`${BASE}/api/settings/integration-agent/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
    });
  } catch {
    return sendIntegrationAgentMessage(message, history);
  }
  if (!res.ok || !res.body?.getReader) {
    if (res.status === 404 || !res.body?.getReader) {
      return sendIntegrationAgentMessage(message, history);
    }
    return jsonOrThrow(res, "The integration agent could not answer that.");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  let failure = "";

  /* SSE frames are separated by a blank line; a frame may straddle two chunks,
     so only whole frames are consumed and the remainder stays buffered. */
  const consume = (frame) => {
    let event = "message";
    const data = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (data.length === 0) return;
    let payload;
    try {
      payload = JSON.parse(data.join("\n"));
    } catch {
      return;
    }
    if (event === "progress") onProgress?.(payload);
    else if (event === "result") result = payload;
    else if (event === "error") failure = String(payload || "");
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      consume(buffer.slice(0, split));
      buffer = buffer.slice(split + 2);
    }
  }
  if (buffer.trim()) consume(buffer);

  if (failure) throw new Error(failure);
  if (!result) throw new Error("The integration agent could not answer that.");
  return result;
}

/* Vendor marks for candidates this deployment has benchmarked, as data URIs.
   The bundled manifest can only cover tools that existed when the frontend was
   built, so anything benchmarked since is resolved by the backend instead. */
export async function fetchBrandLogos(names) {
  const wanted = [...new Set((names || []).filter(Boolean))];
  if (wanted.length === 0) return {};
  /* The endpoint deliberately caps one request at 24 names. Sending the whole
     leaderboard at once used to leave everything after name 24 unprocessed,
     then the caller mistook those omissions for genuine misses and cached
     monograms for a day. Every requested name must reach the resolver. */
  const batches = [];
  for (let index = 0; index < wanted.length; index += 24) {
    batches.push(wanted.slice(index, index + 24));
  }
  const responses = await Promise.all(
    batches.map(async (batch) => {
      const res = await apiFetch(`${BASE}/api/brand?names=${encodeURIComponent(batch.join(","))}`);
      const data = await jsonOrThrow(res);
      return data?.logos && typeof data.logos === "object" ? data.logos : {};
    }),
  );
  return Object.assign({}, ...responses);
}

export async function createSession() {
  const res = await apiFetch(`${BASE}/api/sessions`, { method: "POST" });
  return jsonOrThrow(res);
}

export async function deleteSession(sessionId) {
  const res = await apiFetch(`${BASE}/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  return jsonOrThrow(res);
}

export async function getSession(sessionId) {
  const res = await apiFetch(`${BASE}/api/sessions/${encodeURIComponent(sessionId)}`);
  return jsonOrThrow(res);
}

export async function getResults(runId) {
  const res = await apiFetch(`${BASE}/api/runs/${encodeURIComponent(runId)}/results`);
  return jsonOrThrow(res);
}

export function getReportPdfUrl(runId, download = false) {
  const suffix = download ? "?download=true" : "";
  return `${BASE}/api/runs/${encodeURIComponent(runId)}/report.pdf${suffix}`;
}

export async function prepareReportPdf(runId, download = false) {
  await ensureAuthSession();
  return getReportPdfUrl(runId, download);
}

export async function getHealth() {
  const res = await apiFetch(`${BASE}/api/health`);
  return jsonOrThrow(res);
}

export async function getProviderReadiness() {
  const res = await apiFetch(`${BASE}/api/providers`);
  return jsonOrThrow(res, "Could not load provider readiness.");
}

export async function listProviderKeys() {
  const res = await apiFetch(`${BASE}/api/settings/provider-keys`);
  return jsonOrThrow(res, "Could not load provider credentials.");
}

export async function saveProviderKey(env, value) {
  const res = await apiFetch(`${BASE}/api/settings/provider-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ env, value }),
  });
  return jsonOrThrow(res, "Could not save this provider credential.");
}

export async function deleteProviderKey(env) {
  const res = await apiFetch(`${BASE}/api/settings/provider-keys/${encodeURIComponent(env)}`, {
    method: "DELETE",
  });
  return jsonOrThrow(res, "Could not remove this provider credential.");
}

/* Deliberately a POST for a read: the server accepts the session cookie only on
   GET/HEAD, so POSTing forces a real credential on the one endpoint that hands
   back a secret. Never cache or log the result. */
export async function revealProviderKey(env) {
  const res = await apiFetch(`${BASE}/api/settings/provider-keys/reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ env }),
  });
  return jsonOrThrow(res, "Could not read this provider credential.");
}

/* Ask the integration agent which values a non-secret setting can take. POST
   because it does real work (search, scrape, one model call), not because it
   writes anything. */
export async function getSettingOptions(env) {
  const res = await apiFetch(`${BASE}/api/settings/setting-options`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ env }),
  });
  return jsonOrThrow(res, "Could not research values for this setting.");
}

export async function getSettingsDefaults() {
  const res = await apiFetch(`${BASE}/api/settings/defaults`);
  return jsonOrThrow(res, "Could not load the default providers.");
}

export async function saveSettingsDefaults(changes) {
  const res = await apiFetch(`${BASE}/api/settings/defaults`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return jsonOrThrow(res, "Could not save the default providers.");
}
