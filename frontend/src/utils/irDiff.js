// compute a structural diff between two BpmnDiagram IR objects
// returns { added: string[], modified: string[], removed: RemovedElement[] }
// where each value is a list of element IDs (added/modified) or objects (removed)
//
// The comparison covers every semantic field the delivered edit operations can
// change. It used to look only at `name` and `type`, so a proposal that
// rerouted a flow, set a branch condition, or renamed a process produced an
// empty summary — the reviewer was asked to approve a change the diff did not
// show. Geometry is deliberately excluded: no edit operation moves a shape, and
// the fallback layout assigns coordinates to new elements, which would report a
// move on every addition.

function collectElements(diagram) {
  const map = new Map();
  for (const process of diagram.processes ?? []) {
    for (const node of process.flow_nodes ?? []) {
      map.set(node.id, { ...node, _kind: "node" });
    }
    for (const flow of process.sequence_flows ?? []) {
      map.set(flow.id, { ...flow, _kind: "flow" });
    }
  }
  return map;
}

function collectProcesses(diagram) {
  const map = new Map();
  for (const process of diagram.processes ?? []) {
    map.set(process.id, process);
  }
  return map;
}

function elementKind(element) {
  if (element._kind === "flow" || element.source_ref) return "sequenceFlow";
  return element.type ?? "unknown";
}

function describeElement(element) {
  return {
    id: element.id,
    name: element.name ?? null,
    type: elementKind(element),
    label: element.name ? `${element.name} (${element.id})` : element.id,
  };
}

function show(value) {
  if (value === null || value === undefined || value === "") return "<empty>";
  return String(value);
}

// the semantic fields, per element kind, that a reviewer needs to see change
const NODE_FIELDS = [
  ["type", "type"],
  ["name", "name"],
];

const FLOW_FIELDS = [
  ["name", "name"],
  ["source_ref", "source"],
  ["target_ref", "target"],
  ["condition_expression", "condition"],
];

const PROCESS_FIELDS = [
  ["name", "name"],
  ["is_executable", "executable"],
];

function fieldChanges(oldEl, newEl, fields) {
  const changes = [];
  for (const [key, label] of fields) {
    if (oldEl[key] !== newEl[key]) {
      changes.push(`${label}: ${show(oldEl[key])} -> ${show(newEl[key])}`);
    }
  }
  return changes;
}

function elementChanges(oldEl, newEl) {
  const isFlow = elementKind(newEl) === "sequenceFlow";
  return fieldChanges(oldEl, newEl, isFlow ? FLOW_FIELDS : NODE_FIELDS);
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
    } else if (elementChanges(oldElements.get(id), newEl).length > 0) {
      modified.push(id);
    }
  }

  for (const [id, oldEl] of oldElements) {
    if (!newElements.has(id)) {
      removed.push({
        id,
        name: oldEl.name ?? null,
        type: elementKind(oldEl),
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
      if (elementKind(newEl) === "sequenceFlow") {
        lines.push(`    ${show(newEl.source_ref)} -> ${show(newEl.target_ref)}`);
        if (newEl.condition_expression) {
          lines.push(`    condition: ${newEl.condition_expression}`);
        }
      }
      continue;
    }

    const changes = elementChanges(oldElements.get(id), newEl);
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

  lines.push(...processDiffLines(oldDiagram, newDiagram));

  return lines.length > 0 ? lines.join("\n") : "  no diagram changes detected";
}

function processDiffLines(oldDiagram, newDiagram) {
  const oldProcesses = collectProcesses(oldDiagram);
  const newProcesses = collectProcesses(newDiagram);
  const lines = [];

  for (const [id, newProcess] of newProcesses) {
    if (!oldProcesses.has(id)) {
      lines.push(`+ process ${id}`);
      continue;
    }
    const changes = fieldChanges(
      oldProcesses.get(id),
      newProcess,
      PROCESS_FIELDS,
    );
    if (changes.length > 0) {
      lines.push(`~ process ${id}`);
      for (const change of changes) lines.push(`    ${change}`);
    }
  }

  for (const id of oldProcesses.keys()) {
    if (!newProcesses.has(id)) lines.push(`- process ${id}`);
  }

  return lines;
}
