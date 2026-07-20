const BASE = "http://localhost:8000";

async function jsonOrThrow(res) {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json();
}

export async function postChat(message, sessionId, datasetId, mode = "demo") {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId || undefined,
      dataset_id: datasetId || undefined,
      mode,
    }),
  });
  return jsonOrThrow(res);
}

export async function uploadDataset({ images, groundTruth, useSynthetic }) {
  if (useSynthetic) {
    const res = await fetch(`${BASE}/api/datasets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_synthetic: true }),
    });
    return jsonOrThrow(res);
  }
  const form = new FormData();
  (images || []).forEach((f) => form.append("images", f));
  if (groundTruth) form.append("ground_truth", groundTruth);
  const res = await fetch(`${BASE}/api/datasets`, { method: "POST", body: form });
  return jsonOrThrow(res);
}

export async function startRun(sessionId, spec, mode = "demo") {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spec, mode }),
  });
  return jsonOrThrow(res);
}

export async function stopRun(sessionId) {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}/stop`, { method: "POST" });
  return jsonOrThrow(res);
}

export function openEvents(sessionId) {
  return new EventSource(`${BASE}/api/sessions/${sessionId}/events`);
}

export async function listSessions() {
  const res = await fetch(`${BASE}/api/sessions`);
  return jsonOrThrow(res);
}

export async function createSession() {
  const res = await fetch(`${BASE}/api/sessions`, { method: "POST" });
  return jsonOrThrow(res);
}

export async function deleteSession(sessionId) {
  const res = await fetch(`${BASE}/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  return jsonOrThrow(res);
}

export async function getSession(sessionId) {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}`);
  return jsonOrThrow(res);
}

export async function getResults(runId) {
  const res = await fetch(`${BASE}/api/runs/${runId}/results`);
  return jsonOrThrow(res);
}

export function getReportPdfUrl(runId, download = false) {
  const suffix = download ? "?download=true" : "";
  return `${BASE}/api/runs/${encodeURIComponent(runId)}/report.pdf${suffix}`;
}

export async function getHealth() {
  const res = await fetch(`${BASE}/api/health`);
  return jsonOrThrow(res);
}

export async function listProviderKeys() {
  const res = await fetch(`${BASE}/api/settings/provider-keys`);
  return jsonOrThrow(res);
}

export async function saveProviderKey(env, value) {
  const res = await fetch(`${BASE}/api/settings/provider-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ env, value }),
  });
  return jsonOrThrow(res);
}

export async function deleteProviderKey(env) {
  const res = await fetch(`${BASE}/api/settings/provider-keys/${encodeURIComponent(env)}`, {
    method: "DELETE",
  });
  return jsonOrThrow(res);
}
