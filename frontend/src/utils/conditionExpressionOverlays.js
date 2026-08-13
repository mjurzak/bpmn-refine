export const CONDITION_EXPRESSION_OVERLAY_TYPE = "condition-expression";
export const CONDITION_EXPRESSION_MAX_LABEL_LENGTH = 36;

function isSequenceFlow(element) {
  // External labels share the sequence flow's business object in bpmn-js.
  // Require connection geometry so the label element does not get a duplicate overlay.
  if (!Array.isArray(element?.waypoints)) return false;

  return (
    element?.type === "bpmn:SequenceFlow" ||
    element?.businessObject?.$type === "bpmn:SequenceFlow"
  );
}

export function getConditionExpressionBody(element) {
  const body = element?.businessObject?.conditionExpression?.body;
  return typeof body === "string" ? body : null;
}

export function formatConditionExpression(
  body,
  maxLength = CONDITION_EXPRESSION_MAX_LABEL_LENGTH,
) {
  const value = String(body ?? "").trim();
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(1, maxLength - 1))}…`;
}

function getConnectionBounds(waypoints) {
  return waypoints.reduce(
    (bounds, point) => ({
      minX: Math.min(bounds.minX, point.x),
      minY: Math.min(bounds.minY, point.y),
      maxX: Math.max(bounds.maxX, point.x),
      maxY: Math.max(bounds.maxY, point.y),
    }),
    { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity },
  );
}

/**
 * Return an overlay position relative to the connection bounding box.
 * The longest segment gives routed flows a stable, uncluttered label anchor.
 */
export function getConditionExpressionLabelPosition(connection, offset = 12) {
  const waypoints = connection?.waypoints;
  if (!Array.isArray(waypoints) || waypoints.length < 2) {
    return { left: 0, top: 0 };
  }

  const bounds = getConnectionBounds(waypoints);
  let longestSegment = null;

  for (let index = 1; index < waypoints.length; index += 1) {
    const start = waypoints[index - 1];
    const end = waypoints[index];
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length = Math.hypot(dx, dy);
    if (!length || (longestSegment && length <= longestSegment.length)) continue;

    longestSegment = { start, end, dx, dy, length };
  }

  if (!longestSegment) {
    return {
      left: (bounds.maxX - bounds.minX) / 2,
      top: (bounds.maxY - bounds.minY) / 2,
    };
  }

  const { start, end, dx, dy, length } = longestSegment;
  const midpoint = {
    x: start.x + dx / 2,
    y: start.y + dy / 2,
  };

  return {
    left: midpoint.x - bounds.minX - (dy / length) * offset,
    top: midpoint.y - bounds.minY + (dx / length) * offset,
  };
}

export function createConditionExpressionLabel(body) {
  const fullValue = String(body ?? "");
  const label = document.createElement("span");
  label.className = "condition-expression-overlay";
  label.textContent = formatConditionExpression(fullValue);
  label.title = fullValue;
  label.setAttribute("aria-label", `Condition expression: ${fullValue}`);
  return label;
}

export function createConditionExpressionOverlayManager(viewer) {
  let overlayIds = [];

  function clear() {
    const overlays = viewer?.get?.("overlays");
    if (overlays) {
      for (const id of overlayIds) overlays.remove(id);
    }
    overlayIds = [];
  }

  function render() {
    clear();

    const overlays = viewer?.get?.("overlays");
    const registry = viewer?.get?.("elementRegistry");
    if (!overlays || !registry) return;

    for (const element of registry.getAll?.() ?? []) {
      if (!isSequenceFlow(element)) continue;

      const body = getConditionExpressionBody(element);
      if (!body || !body.trim()) continue;

      const id = overlays.add(element, CONDITION_EXPRESSION_OVERLAY_TYPE, {
        html: createConditionExpressionLabel(body),
        position: getConditionExpressionLabelPosition(element),
        scale: true,
      });
      overlayIds.push(id);
    }
  }

  return { clear, render, destroy: clear };
}
