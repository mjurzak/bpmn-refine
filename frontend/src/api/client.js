const BASE = "/api/v1";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? res.statusText);
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

export async function validateDiagram(diagram, includeSemantic = false) {
  return request("/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ diagram, include_semantic: includeSemantic }),
  });
}

export async function sendChatMessage(messages, diagram = null, issues = []) {
  return request("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, diagram, issues }),
  });
}
