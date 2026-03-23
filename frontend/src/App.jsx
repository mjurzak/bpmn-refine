import React, { useState, useCallback } from "react";
import BpmnEditor from "./components/BpmnEditor.jsx";
import ValidationPanel from "./components/ValidationPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import { uploadDiagram, validateDiagram, exportDiagram } from "./api/client.js";

export default function App() {
  const [xml, setXml] = useState(null);
  const [ir, setIr] = useState(null);
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
      setIr(res.ir);
      // convert IR back to xml so the editor shows the parsed result
      const exported = await exportDiagram(res.ir);
      setXml(exported.xml);
    } catch (err) {
      alert(`Upload failed: ${err.message}`);
    }
  }

  async function handleValidate() {
    if (!ir) return alert("Upload a diagram first.");
    setValidating(true);
    try {
      const res = await validateDiagram(ir, includeSemantic);
      setValidationResult(res);
    } catch (err) {
      alert(`Validation failed: ${err.message}`);
    } finally {
      setValidating(false);
    }
  }

  // called when the chat assistant proposes an IR update
  function handleIrUpdate(updatedIr) {
    setIr(updatedIr);
    exportDiagram(updatedIr)
      .then((res) => setXml(res.xml))
      .catch(console.error);
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
              ir={ir}
              issues={[...(validationResult?.issues ?? []), ...(validationResult?.semantic_issues ?? [])]}
              onIrUpdate={handleIrUpdate}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
