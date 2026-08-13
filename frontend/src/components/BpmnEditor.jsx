import { useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import BpmnModeler from "bpmn-js/lib/Modeler";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";
import { createConditionExpressionOverlayManager } from "../utils/conditionExpressionOverlays.js";

const DEFAULT_DIAGRAM = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  id="Definitions_1"
                  targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_1" isExecutable="false">
    <bpmn:startEvent id="StartEvent_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
      <bpmndi:BPMNShape id="_BPMNShape_StartEvent_2" bpmnElement="StartEvent_1">
        <dc:Bounds x="152" y="82" width="36" height="36" />
      </bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;

const DIFF_MARKERS = ["diff-added", "diff-modified", "diff-selected"];

// bpmn-js Modeler as a React component, with diff highlighting exposed via ref
const BpmnEditor = forwardRef(function BpmnEditor({ xml, onXmlChange }, ref) {
  const containerRef = useRef(null);
  const modelerRef = useRef(null);
  const conditionOverlaysRef = useRef(null);
  const importTokenRef = useRef(0);

  // stable reference so the commandStack listener doesn't go stale
  const onXmlChangeRef = useRef(onXmlChange);
  onXmlChangeRef.current = onXmlChange;

  useEffect(() => {
    const modeler = new BpmnModeler({ container: containerRef.current });
    modelerRef.current = modeler;
    const conditionOverlays = createConditionExpressionOverlayManager(modeler);
    conditionOverlaysRef.current = conditionOverlays;

    modeler.on("commandStack.changed", async () => {
      if (!modelerRef.current) return;
      conditionOverlays.render();
      try {
        const { xml: updatedXml } = await modeler.saveXML({ format: true });
        onXmlChangeRef.current?.(updatedXml);
      } catch (err) {
        console.error("failed to export xml", err);
      }
    });

    return () => {
      conditionOverlays.destroy();
      conditionOverlaysRef.current = null;
      modelerRef.current = null;
      modeler.destroy();
    };
  }, []);

  useEffect(() => {
    const modeler = modelerRef.current;
    const conditionOverlays = conditionOverlaysRef.current;
    if (!modeler || !conditionOverlays) return undefined;

    let cancelled = false;
    const importToken = ++importTokenRef.current;
    conditionOverlays.clear();
    modeler
      .importXML(xml || DEFAULT_DIAGRAM)
      .then(() => {
        if (
          !cancelled &&
          importTokenRef.current === importToken &&
          modelerRef.current === modeler
        ) {
          conditionOverlays.render();
        }
      })
      .catch((reason) => {
        if (!cancelled) console.error(reason);
      });

    return () => {
      cancelled = true;
    };
  }, [xml]);

  useImperativeHandle(ref, () => ({
    async getXml() {
      if (!modelerRef.current) return null;
      const { xml: currentXml } = await modelerRef.current.saveXML({ format: true });
      return currentXml;
    },

    importXml(xml) {
      const modeler = modelerRef.current;
      const conditionOverlays = conditionOverlaysRef.current;
      if (!modeler || !conditionOverlays) return;
      const importToken = ++importTokenRef.current;
      conditionOverlays.clear();
      modeler
        .importXML(xml)
        .then(() => {
          if (
            importTokenRef.current === importToken &&
            modelerRef.current === modeler
          ) {
            conditionOverlays.render();
          }
        })
        .catch(console.error);
    },

    // bpmn-js measures zero while the pane is display:none, so refit on return
    resize() {
      const modeler = modelerRef.current;
      if (!modeler) return;
      try {
        const canvas = modeler.get("canvas");
        canvas.resized();
        canvas.zoom("fit-viewport", "auto");
      } catch (err) {
        console.error("failed to resize canvas", err);
      }
    },

    applyDiff({ added = [], modified = [] }) {
      const modeler = modelerRef.current;
      if (!modeler) return;
      const canvas = modeler.get("canvas");
      const elementRegistry = modeler.get("elementRegistry");

      for (const id of added) {
        if (elementRegistry.get(id)) canvas.addMarker(id, "diff-added");
      }
      for (const id of modified) {
        if (elementRegistry.get(id)) canvas.addMarker(id, "diff-modified");
      }
    },

    clearDiff() {
      const modeler = modelerRef.current;
      if (!modeler) return;
      const canvas = modeler.get("canvas");
      const elementRegistry = modeler.get("elementRegistry");

      for (const { id } of elementRegistry.getAll()) {
        for (const marker of DIFF_MARKERS) {
          canvas.removeMarker(id, marker);
        }
      }
    },

    highlightElements(ids = []) {
      const modeler = modelerRef.current;
      if (!modeler) return;
      const canvas = modeler.get("canvas");
      const elementRegistry = modeler.get("elementRegistry");

      for (const { id } of elementRegistry.getAll()) {
        canvas.removeMarker(id, "diff-selected");
      }
      for (const id of ids) {
        if (elementRegistry.get(id)) canvas.addMarker(id, "diff-selected");
      }
    },
  }));

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: "100%" }}
    />
  );
});

export default BpmnEditor;
