// Dependency analysis over an atomic repair plan.
//
// Operations are applied in order, so one can depend on another: an `add_flow`
// whose endpoint is a node an earlier `add_node` creates cannot be applied on
// its own. The review gate lets a user deselect individual operations, and
// until now the consequence only surfaced as an error from `/repair/apply`
// after the fact — the user picked a subset, pressed Apply, and was told it was
// impossible. Reporting the dependency up front makes the choice informed.

// which element IDs an operation introduces
function producedIds(op) {
  switch (op?.op) {
    case "add_node":
    case "add_flow":
      return op.id ? [op.id] : [];
    default:
      return [];
  }
}

// which element IDs an operation requires to already exist
function requiredIds(op) {
  switch (op?.op) {
    case "add_flow":
      return [op.source_ref, op.target_ref].filter(Boolean);
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

/**
 * Map each operation index to the indexes it depends on.
 *
 * An operation depends on an earlier one when that earlier operation creates an
 * ID it references. Only IDs the plan itself introduces count — an operation
 * addressing an element that already exists in the base diagram depends on
 * nothing.
 *
 * @returns {Map<number, number[]>} index -> indexes that must also be selected
 */
export function operationDependencies(ops = []) {
  const dependencies = new Map();
  // id -> index of the operation that introduces it
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

/**
 * Find selected operations whose dependencies are not also selected.
 *
 * @returns {{index: number, missing: number[]}[]} one entry per broken operation
 */
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

/**
 * Grow a selection until every dependency it relies on is included.
 *
 * Used when a user selects an operation that needs an earlier one: rather than
 * refusing the choice, the panel offers to pull in what it requires.
 */
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

/** Human-readable explanation of one broken dependency, for the review panel. */
export function describeUnmetDependency({ index, missing }) {
  const listed = missing.map((item) => `#${item + 1}`).join(", ");
  return `Operation #${index + 1} needs ${listed} to be selected as well.`;
}
