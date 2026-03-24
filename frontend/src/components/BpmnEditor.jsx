import React, { useEffect, useRef, useCallback } from "react";
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

// wraps bpmn-js Modeler as a React component
export default function BpmnEditor({ xml, onXmlChange }) {
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
      if (!modelerRef.current) return; // destroyed by cleanup
      try {
        const { xml: updatedXml } = await modeler.saveXML({ format: true });
        onXmlChangeRef.current?.(updatedXml);
      } catch (err) {
        console.error("failed to export xml", err);
      }
    });

    return () => {
      // clear ref BEFORE destroy so other effects see null and bail out
      modelerRef.current = null;
      modeler.destroy();
    };
  }, []);

  // re-import when xml prop changes from outside (e.g. chat applying a diagram)
  useEffect(() => {
    if (!xml || !modelerRef.current) return;
    modelerRef.current.importXML(xml).catch(console.error);
  }, [xml]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: "100%" }}
    />
  );
}
