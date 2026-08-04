// Presentation helpers over an atomic repair plan.
//
// One copy for the chat proposal list and the canvas review panel — two copies
// is how one of them came to handle `rename_element`, which the backend has
// never emitted. Authoritative list: `EditOpType` in backend/app/repair/ops.py.

/** One-line human description of an operation, for a review list. */
export function opLabel(op) {
  switch (op?.op) {
    case "add_node":
      return `Add ${op.node_type} ${op.name ? `"${op.name}"` : op.id}`;
    case "remove_node":
      return `Remove node ${op.id}${op.cascade ? " and incident flows" : ""}`;
    case "add_flow":
      return `Add flow ${op.id}: ${op.source_ref} -> ${op.target_ref}`;
    case "remove_flow":
      return `Remove flow ${op.id}`;
    case "rename_node":
      return `Rename node ${op.id} to "${op.new_name}"`;
    case "rename_flow":
      return `Rename flow ${op.id} to "${op.new_name}"`;
    case "change_node_type":
      return `Change ${op.id} to ${op.new_type}`;
    case "change_gateway_type":
      return `Change gateway ${op.id} to ${op.new_type}`;
    case "set_condition":
      return op.condition_expression
        ? `Set condition on ${op.flow_id}`
        : `Clear condition on ${op.flow_id}`;
    case "replace_diagram":
      return "Replace full diagram";
    default:
      return op?.op ?? "unknown operation";
  }
}

/**
 * The diagram elements an operation touches, for highlighting on the canvas.
 *
 * `replace_diagram` returns nothing on purpose — highlighting everything says
 * as little as highlighting nothing.
 */
export function opElementIds(op) {
  switch (op?.op) {
    case "add_node":
    case "remove_node":
    case "rename_node":
    case "change_node_type":
    case "change_gateway_type":
      return [op.id].filter(Boolean);
    case "remove_flow":
    case "rename_flow":
      return [op.id].filter(Boolean);
    case "add_flow":
      return [op.id, op.source_ref, op.target_ref].filter(Boolean);
    case "set_condition":
      return [op.flow_id].filter(Boolean);
    default:
      return [];
  }
}
