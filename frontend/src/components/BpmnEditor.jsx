import { useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import BpmnModeler from "bpmn-js/lib/Modeler";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";

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

// wraps bpmn-js Modeler as a React component
// exposes applyDiff / clearDiff via ref for hover-based diff highlighting
const BpmnEditor = forwardRef(function BpmnEditor({ xml, onXmlChange }, ref) {
  const containerRef = useRef(null);
  const modelerRef = useRef(null);

  // stable reference so the commandStack listener doesn't go stale
  const onXmlChangeRef = useRef(onXmlChange);
  onXmlChangeRef.current = onXmlChange;

  useEffect(() => {
    const modeler = new BpmnModeler({ container: containerRef.current });
    modelerRef.current = modeler;

    modeler.importXML(xml || DEFAULT_DIAGRAM).catch(console.error);

    modeler.on("commandStack.changed", async () => {
      if (!modelerRef.current) return;
      try {
        const { xml: updatedXml } = await modeler.saveXML({ format: true });
        onXmlChangeRef.current?.(updatedXml);
      } catch (err) {
        console.error("failed to export xml", err);
      }
    });

    return () => {
      modelerRef.current = null;
      modeler.destroy();
    };
  }, []);

  useEffect(() => {
    if (!xml || !modelerRef.current) return;
    modelerRef.current.importXML(xml).catch(console.error);
  }, [xml]);

  // expose diff marker controls and direct import to parent via ref
  useImperativeHandle(ref, () => ({
    importXml(xml) {
      if (!modelerRef.current) return;
      modelerRef.current.importXML(xml).catch(console.error);
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
