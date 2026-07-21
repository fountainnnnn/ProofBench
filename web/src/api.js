import { safeVisibleText, sanitizeForDisplay } from "./displaySafety.js";

const configuredBase = String(import.meta.env.VITE_API_BASE_URL || "").trim();
const BASE = configuredBase === "/"
  ? ""
  : configuredBase.replace(/\/+$/, "");
export const AUTH_CHANGE_EVENT = "proofbench-auth-change";
// The only execution mode this client can request. It is a module constant
// rather than component state so no UI path can make a write non-real.
export const RUN_MODE = "real";
// The browser console is local-profile only. It holds no credential of any
// kind, so this flag is false until the server reports `auth_mode: "local"`.
// An authenticated or unreachable deployment fails closed: there is no browser
// path that can supply a token to open it.
export const LOCAL_PROFILE_REQUIRED =
  "The browser console is available only on a local ProofBench profile.";
let localMode = false;

export function isLocalMode() {
  return localMode;
}

function emitAuthChange(context) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(AUTH_CHANGE_EVENT, { detail: context }));
  }
}

export async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  // The browser never holds a credential, so no Authorization header is ever
  // attached. API clients supply their own bearer or API key directly.
  const response = await fetch(url, { ...options, headers, credentials: "include" });
  if (response.status === 401) {
    localMode = false;
    emitAuthChange({ localMode: false });
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

// Read-only probe of the deployment profile. It sends no credential and
// mutates no session: the browser console either runs against a local profile
// or it does not run at all.
export async function bootstrapAuthSession() {
  const response = await fetch(`${BASE}/api/auth/session`, { credentials: "include" });
  if (!response.ok) {
    localMode = false;
    emitAuthChange({ localMode: false });
    throw new Error(LOCAL_PROFILE_REQUIRED);
  }
  const body = await jsonOrThrow(response);
  // Local tokenless profile: the server resolves every caller to the local
  // tenant, so there is no credential to hold, restore, or discard.
  if (body?.auth_mode !== "local") {
    localMode = false;
    emitAuthChange({ localMode: false });
    throw new Error(LOCAL_PROFILE_REQUIRED);
  }
  localMode = true;
  const local = { localMode: true };
  emitAuthChange(local);
  return local;
}

let authRefreshPromise = null;

export async function ensureAuthSession() {
  if (!authRefreshPromise) {
    authRefreshPromise = bootstrapAuthSession()
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
  return jsonOrThrow(res);
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
