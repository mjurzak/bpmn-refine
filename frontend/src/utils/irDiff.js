// compute a structural diff between two BpmnDiagram IR objects
// returns { added: string[], modified: string[], removed: RemovedElement[] }
// where each value is a list of element IDs (added/modified) or objects (removed)

function collectElements(diagram) {
  const map = new Map();
  for (const process of diagram.processes ?? []) {
    for (const node of process.flow_nodes ?? []) {
      map.set(node.id, node);
    }
    for (const flow of process.sequence_flows ?? []) {
      map.set(flow.id, flow);
    }
  }
  return map;
}

function describeElement(element) {
  const kind = element.type ?? (element.source_ref ? "sequenceFlow" : "unknown");
  return {
    id: element.id,
    name: element.name ?? null,
    type: kind,
    label: element.name ? `${element.name} (${element.id})` : element.id,
  };
}

export function diffDiagrams(oldDiagram, newDiagram) {
  if (!oldDiagram || !newDiagram) return { added: [], modified: [], removed: [] };

  const oldElements = collectElements(oldDiagram);
  const newElements = collectElements(newDiagram);

  const added = [];
  const modified = [];
  const removed = [];

  for (const [id, newEl] of newElements) {
    if (!oldElements.has(id)) {
      added.push(id);
    } else {
      const oldEl = oldElements.get(id);
      // check for meaningful changes: name or type
      if (oldEl.name !== newEl.name || oldEl.type !== newEl.type) {
        modified.push(id);
      }
    }
  }

  for (const [id, oldEl] of oldElements) {
    if (!newElements.has(id)) {
      removed.push({
        id,
        name: oldEl.name ?? null,
        type: oldEl.type ?? oldEl.source_ref ? "sequenceFlow" : "unknown",
      });
    }
  }

  return { added, modified, removed };
}

export function summarizeDiagramDiff(oldDiagram, newDiagram) {
  if (!oldDiagram || !newDiagram) return "  no diagram changes detected";

  const oldElements = collectElements(oldDiagram);
  const newElements = collectElements(newDiagram);
  const lines = [];

  for (const [id, newEl] of newElements) {
    if (!oldElements.has(id)) {
      const element = describeElement(newEl);
      lines.push(`+ ${element.type} ${element.label}`);
      continue;
    }

    const oldEl = oldElements.get(id);
    const changes = [];

    if (oldEl.type !== newEl.type) {
      changes.push(`type: ${oldEl.type ?? "unknown"} -> ${newEl.type ?? "unknown"}`);
    }
    if (oldEl.name !== newEl.name) {
      changes.push(`name: ${oldEl.name ?? "<empty>"} -> ${newEl.name ?? "<empty>"}`);
    }

    if (changes.length > 0) {
      lines.push(`~ ${describeElement(newEl).label}`);
      for (const change of changes) {
        lines.push(`    ${change}`);
      }
    }
  }

  for (const [id, oldEl] of oldElements) {
    if (!newElements.has(id)) {
      const element = describeElement(oldEl);
      lines.push(`- ${element.type} ${element.label}`);
    }
  }

  return lines.length > 0 ? lines.join("\n") : "  no diagram changes detected";
}
