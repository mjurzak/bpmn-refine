import React, { useState, useCallback } from "react";
import { layoutProcess } from "bpmn-auto-layout";
import BpmnEditor from "./components/BpmnEditor.jsx";
import ValidationPanel from "./components/ValidationPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import { uploadDiagram, validateDiagram, exportDiagram } from "./api/client.js";

// run bpmn-auto-layout on raw XML, fall back to the original if it fails
async function autoLayout(xmlString) {
  try {
    return await layoutProcess(xmlString);
  } catch (err) {
    console.warn("auto-layout failed, using raw XML:", err);
    return xmlString;
  }
}

export default function App() {
  const [xml, setXml] = useState(null);
  const [diagram, setDiagram] = useState(null);
  const [validationResult, setValidationResult] = useState(null);
  const [validating, setValidating] = useState(false);
  const [includeSemantic, setIncludeSemantic] = useState(false);

  // when the user edits in the canvas we update xml but don't auto-validate
  const handleXmlChange = useCallback((updatedXml) => {
    setXml(updatedXml);
  }, []);

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const res = await uploadDiagram(file);
      setDiagram(res.diagram);
      const exported = await exportDiagram(res.diagram);
      const laid = await autoLayout(exported.xml);
      setXml(laid);
    } catch (err) {
      alert(`Upload failed: ${err.message}`);
    }
  }

  async function handleValidate() {
    if (!diagram) return alert("Upload a diagram first.");
    setValidating(true);
    try {
      const res = await validateDiagram(diagram, includeSemantic);
      setValidationResult(res);
    } catch (err) {
      alert(`Validation failed: ${err.message}`);
    } finally {
      setValidating(false);
    }
  }

  // called when the chat assistant proposes a diagram update
  async function handleDiagramUpdate(updatedDiagram) {
    setDiagram(updatedDiagram);
    try {
      const exported = await exportDiagram(updatedDiagram);
      const laid = await autoLayout(exported.xml);
      setXml(laid);
    } catch (err) {
      console.error("failed to apply diagram update:", err);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      {/* toolbar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          padding: "8px 16px",
          background: "#1976d2",
          color: "#fff",
        }}
      >
        <strong>BPMN AI Validator</strong>
        <label
          style={{
            cursor: "pointer",
            background: "#fff",
            color: "#1976d2",
            padding: "4px 10px",
            borderRadius: "4px",
            fontSize: "0.85em",
          }}
        >
          Upload .bpmn
          <input type="file" accept=".bpmn" onChange={handleUpload} style={{ display: "none" }} />
        </label>
        <label style={{ fontSize: "0.85em", display: "flex", alignItems: "center", gap: "4px" }}>
          <input
            type="checkbox"
            checked={includeSemantic}
            onChange={(e) => setIncludeSemantic(e.target.checked)}
          />
          Semantic (LLM)
        </label>
        <button
          onClick={handleValidate}
          style={{
            background: "#fff",
            color: "#1976d2",
            border: "none",
            padding: "4px 10px",
            borderRadius: "4px",
            cursor: "pointer",
            fontSize: "0.85em",
          }}
        >
          Validate
        </button>
      </div>

      {/* main area */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* bpmn editor — takes most space */}
        <div style={{ flex: 3 }}>
          <BpmnEditor xml={xml} onXmlChange={handleXmlChange} />
        </div>

        {/* right sidebar */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            borderLeft: "1px solid #ddd",
            minWidth: "280px",
            maxWidth: "380px",
          }}
        >
          <div style={{ flex: 1, borderBottom: "1px solid #ddd", overflow: "auto" }}>
            <ValidationPanel
              issues={validationResult?.issues ?? []}
              semanticIssues={validationResult?.semantic_issues ?? []}
              isValid={validationResult?.is_valid}
              loading={validating}
            />
          </div>
          <div style={{ flex: 1, overflow: "hidden" }}>
            <ChatPanel
              ir={diagram}
              issues={[...(validationResult?.issues ?? []), ...(validationResult?.semantic_issues ?? [])]}
              onIrUpdate={handleDiagramUpdate}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
