// dependency analysis over a repair plan: ops apply in order, so an add_flow
// whose endpoint comes from an earlier add_node cannot be selected on its own

function producedIds(op) {
  switch (op?.op) {
    case "add_node":
    case "add_flow":
      return op.id ? [op.id] : [];
    default:
      return [];
  }
}

function requiredIds(op) {
  switch (op?.op) {
    case "add_flow":
      return [op.process_id, op.source_ref, op.target_ref].filter(Boolean);
    case "remove_node":
    case "rename_node":
    case "rename_flow":
    case "remove_flow":
    case "change_node_type":
    case "change_gateway_type":
      return op.id ? [op.id] : [];
    case "set_condition":
      return op.flow_id ? [op.flow_id] : [];
    case "add_node":
      return op.process_id ? [op.process_id] : [];
    default:
      return [];
  }
}

// map each op index to the earlier indexes that create IDs it references, as
// Map<number, number[]>. only IDs the plan itself introduces count
export function operationDependencies(ops = []) {
  const dependencies = new Map();
  const producedBy = new Map();

  ops.forEach((op, index) => {
    const needs = new Set();
    for (const id of requiredIds(op)) {
      const producer = producedBy.get(id);
      if (producer !== undefined) needs.add(producer);
    }
    dependencies.set(index, [...needs].sort((a, b) => a - b));

    for (const id of producedIds(op)) {
      if (!producedBy.has(id)) producedBy.set(id, index);
    }
  });

  return dependencies;
}

export function unmetDependencies(ops = [], selectedIndexes = []) {
  const dependencies = operationDependencies(ops);
  const selected = new Set(selectedIndexes);
  const broken = [];

  for (const index of [...selected].sort((a, b) => a - b)) {
    const missing = (dependencies.get(index) ?? []).filter(
      (needed) => !selected.has(needed),
    );
    if (missing.length > 0) broken.push({ index, missing });
  }

  return broken;
}

export function withDependencies(ops = [], selectedIndexes = []) {
  const dependencies = operationDependencies(ops);
  const resolved = new Set(selectedIndexes);
  const queue = [...resolved];

  while (queue.length > 0) {
    const index = queue.pop();
    for (const needed of dependencies.get(index) ?? []) {
      if (!resolved.has(needed)) {
        resolved.add(needed);
        queue.push(needed);
      }
    }
  }

  return [...resolved].sort((a, b) => a - b);
}

export function describeUnmetDependency({ index, missing }) {
  const listed = missing.map((item) => `#${item + 1}`).join(", ");
  return `Operation #${index + 1} needs ${listed} to be selected as well.`;
}
