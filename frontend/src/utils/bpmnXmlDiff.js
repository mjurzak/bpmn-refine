const BPMN_MODEL_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL";
const DERIVED_CHILDREN = new Set(["incoming", "outgoing"]);
const NON_VISUAL_ELEMENTS = new Set(["definitions", "process", "collaboration"]);

export function compareBpmnXml(originalXml, variantXml) {
  const original = elementSignatures(originalXml);
  const variant = elementSignatures(variantXml);
  const removed = [];
  const added = [];
  const modified = [];

  for (const [id, signature] of original) {
    if (!variant.has(id)) removed.push(id);
    else if (variant.get(id) !== signature) modified.push(id);
  }
  for (const id of variant.keys()) {
    if (!original.has(id)) added.push(id);
  }

  return {
    removed: removed.sort(),
    added: added.sort(),
    modified: modified.sort(),
  };
}

function elementSignatures(xml) {
  if (!xml) return new Map();
  const document = new DOMParser().parseFromString(xml, "application/xml");
  if (document.querySelector("parsererror")) {
    throw new Error("BPMN XML could not be parsed for comparison");
  }

  const signatures = new Map();
  for (const element of document.getElementsByTagNameNS(BPMN_MODEL_NS, "*")) {
    const id = element.getAttribute("id");
    if (
      !id ||
      NON_VISUAL_ELEMENTS.has(element.localName) ||
      element.localName.endsWith("EventDefinition")
    ) {
      continue;
    }
    signatures.set(id, signature(element));
  }
  return signatures;
}

function signature(element) {
  const attributes = [...element.attributes]
    .filter(({ name }) => name !== "id")
    .map(({ namespaceURI, localName, value }) => [namespaceURI ?? "", localName, value])
    .sort(([namespaceA, nameA], [namespaceB, nameB]) =>
      `${namespaceA}:${nameA}`.localeCompare(`${namespaceB}:${nameB}`),
    );
  const children = [...element.children]
    .filter(
      (child) =>
        child.namespaceURI === BPMN_MODEL_NS &&
        !DERIVED_CHILDREN.has(child.localName) &&
        (!child.hasAttribute("id") || child.localName.endsWith("EventDefinition")),
    )
    .map((child) => ({
      type: child.localName,
      attributes: [...child.attributes]
        .map(({ namespaceURI, localName, value }) => [
          namespaceURI ?? "",
          localName,
          value,
        ])
        .sort(),
      text: child.textContent?.trim() ?? "",
    }));

  return JSON.stringify({ type: element.localName, attributes, children });
}
