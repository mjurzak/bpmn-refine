const BASE = "/api/v1";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    const error = new Error(detail.detail ?? res.statusText);
    error.payload = detail;
    throw error;
  }
  return res.json();
}

export async function uploadDiagram(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/diagrams/upload", { method: "POST", body: form });
}

export async function exportDiagram(ir) {
  return request("/diagrams/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ir),
  });
}

export async function parseDiagramXml(xml) {
  return request("/diagrams/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ xml }),
  });
}

export async function validateDiagram(diagram, includeSemantic = false, config = {}) {
  return request("/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ diagram, include_semantic: includeSemantic, config }),
  });
}

export async function sendChatMessage(
  messages,
  diagram = null,
  issues = [],
  sessionId = null,
  snapshotChanges = true,
  config = {},
) {
  return request("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages,
      diagram,
      issues,
      session_id: sessionId,
      snapshot_changes: snapshotChanges,
      config,
    }),
  });
}

export async function repairDiagram(xml, issues = [], config = {}) {
  return request("/repair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ xml, issues, config }),
  });
}

export async function applyEditOps(diagram, ops = []) {
  return request("/repair/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ diagram, ops }),
  });
}

export async function commitRevision(
  diagram,
  sessionId = null,
  message = "accepted proposal",
  author = "llm",
) {
  return request("/history/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ diagram, session_id: sessionId, message, author }),
  });
}

export async function getHistory(sessionId) {
  return request(`/history/${sessionId}`);
}

export async function getRevision(sessionId, revId) {
  return request(`/history/${sessionId}/${revId}`);
}

export async function revertToRevision(sessionId, revId) {
  return request(`/history/${sessionId}/revert/${revId}`, { method: "POST" });
}
