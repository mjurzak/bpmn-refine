import React, { useState, useCallback } from "react";
import { layoutProcess } from "bpmn-auto-layout";
import BpmnEditor from "./components/BpmnEditor.jsx";
import ValidationPanel from "./components/ValidationPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import { uploadDiagram, validateDiagram, exportDiagram } from "./api/client.js";
import useResize from "./hooks/useResize.js";
import "./app.css";

// run bpmn-auto-layout on raw XML, fall back to the original if it fails
async function autoLayout(xmlString) {
  try {
    return await layoutProcess(xmlString);
  } catch (err) {
    console.warn("auto-layout failed, using raw XML:", err);
    return xmlString;
  }
}

// simple arrow icon for the send button
function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

export default function App() {
  const [xml, setXml] = useState(null);
  const [diagram, setDiagram] = useState(null);
  const [validationResult, setValidationResult] = useState(null);
  const [validating, setValidating] = useState(false);
  const [includeSemantic, setIncludeSemantic] = useState(false);

  // resizable sidebar width
  const sidebar = useResize({ initial: 340, min: 260, max: 520, axis: "horizontal" });

  // resizable split between validation (top) and chat (bottom)
  // this is the height of the validation section in px
  const panelSplit = useResize({ initial: 260, min: 80, max: 600, axis: "vertical" });

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

  const allIssues = [
    ...(validationResult?.issues ?? []),
    ...(validationResult?.semantic_issues ?? []),
  ];
  const errorCount = allIssues.filter((i) => i.severity === "error").length;

  return (
    <div className="app-layout">
      {/* toolbar */}
      <div className="toolbar">
        <div className="toolbar-title">
          BPMN <span>AI</span> Validator
        </div>

        <div className="toolbar-spacer" />

        <label className="toolbar-btn">
          Upload .bpmn
          <input
            type="file"
            accept=".bpmn"
            onChange={handleUpload}
            style={{ display: "none" }}
          />
        </label>

        <label className="toolbar-checkbox">
          <input
            type="checkbox"
            checked={includeSemantic}
            onChange={(e) => setIncludeSemantic(e.target.checked)}
          />
          Semantic (LLM)
        </label>

        <button onClick={handleValidate} className="toolbar-btn toolbar-btn--primary">
          Validate
        </button>
      </div>

      {/* main area */}
      <div className="main-area">
        {/* left sidebar */}
        <div className="sidebar" style={{ width: sidebar.size }}>
          {/* validation panel — top */}
          <div className="panel-section" style={{ height: panelSplit.size }}>
            <ValidationPanel
              issues={validationResult?.issues ?? []}
              semanticIssues={validationResult?.semantic_issues ?? []}
              isValid={validationResult?.is_valid}
              loading={validating}
              errorCount={errorCount}
            />
          </div>

          {/* vertical resize handle */}
          <div
            className={`resize-handle-v${panelSplit.isDragging ? " dragging" : ""}`}
            onMouseDown={panelSplit.handleMouseDown}
          />

          {/* chat panel — bottom */}
          <div className="panel-section" style={{ flex: 1 }}>
            <ChatPanel
              ir={diagram}
              issues={allIssues}
              onIrUpdate={handleDiagramUpdate}
              SendIcon={SendIcon}
            />
          </div>
        </div>

        {/* horizontal resize handle */}
        <div
          className={`resize-handle-h${sidebar.isDragging ? " dragging" : ""}`}
          onMouseDown={sidebar.handleMouseDown}
        />

        {/* bpmn editor */}
        <div className="editor-pane">
          <BpmnEditor xml={xml} onXmlChange={handleXmlChange} />
        </div>
      </div>
    </div>
  );
}
