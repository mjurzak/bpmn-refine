import React, { useState, useCallback, useRef, useEffect } from "react";
import { layoutProcess } from "bpmn-auto-layout";
import BpmnEditor from "./components/BpmnEditor.jsx";
import ValidationPanel from "./components/ValidationPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import HistoryPanel from "./components/HistoryPanel.jsx";
import { uploadDiagram, validateDiagram, exportDiagram, revertToRevision } from "./api/client.js";
import useResize from "./hooks/useResize.js";
import "./app.css";

async function autoLayout(xmlString) {
  try {
    return await layoutProcess(xmlString);
  } catch (err) {
    console.warn("auto-layout failed, using raw XML:", err);
    return xmlString;
  }
}

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

export default function App() {
  const [xml, setXml] = useState(null);
  const [diagram, setDiagram] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [currentRevId, setCurrentRevId] = useState(null);
  const [previewRev, setPreviewRev] = useState(null); // { rev_id, diagram, message, index }
  const [validationResult, setValidationResult] = useState(null);
  const [validating, setValidating] = useState(false);
  const [includeSemantic, setIncludeSemantic] = useState(false);
  const [darkMode, setDarkMode] = useState(false);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  const editorRef = useRef(null);

  const sidebar = useResize({ initial: 340, min: 260, max: 520, axis: "horizontal" });
  const validationSplit = useResize({ initial: 220, min: 60, max: 500, axis: "vertical" });
  const chatSplit = useResize({ initial: 280, min: 80, max: 500, axis: "vertical" });

  const handleXmlChange = useCallback((updatedXml) => {
    // ignore canvas changes while previewing a historical snapshot
    if (previewRev) return;
    setXml(updatedXml);
  }, [previewRev]);

  async function applyDiagram(updatedDiagram) {
    setDiagram(updatedDiagram);
    try {
      const exported = await exportDiagram(updatedDiagram);
      const laid = await autoLayout(exported.xml);
      setXml(laid);
    } catch (err) {
      console.error("failed to apply diagram update:", err);
    }
  }

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    dismissPreview();
    try {
      const res = await uploadDiagram(file);
      setDiagram(res.diagram);
      setSessionId(res.session_id);
      setCurrentRevId("0000");
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

  async function handleChatUpdate(updatedDiagram, newRevId, newSessionId) {
    dismissPreview();
    await applyDiagram(updatedDiagram);
    if (newSessionId) setSessionId(newSessionId);
    if (newRevId) setCurrentRevId(newRevId);
  }

  async function handleRevert(revertedDiagram, newRevId) {
    dismissPreview();
    await applyDiagram(revertedDiagram);
    setDiagram(revertedDiagram);
    setCurrentRevId(newRevId);
    setValidationResult(null);
  }

  // called when user clicks a history card — loads it on canvas without committing
  function handlePreview(rev) {
    setPreviewRev(rev);
  }

  function dismissPreview() {
    if (!previewRev) return;
    setPreviewRev(null);
    // restore the current XML on the canvas
    if (xml) editorRef.current?.importXml(xml);
  }

  async function restorePreview() {
    if (!previewRev) return;
    try {
      const res = await revertToRevision(sessionId, previewRev.rev_id);
      await applyDiagram(res.diagram);
      setDiagram(res.diagram);
      setCurrentRevId(res.new_rev_id);
      setValidationResult(null);
      setPreviewRev(null);
    } catch (err) {
      alert(`Restore failed: ${err.message}`);
    }
  }

  function handleExport() {
    if (!xml) return;
    const blob = new Blob([xml], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "diagram.bpmn";
    a.click();
    URL.revokeObjectURL(url);
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
          <input type="file" accept=".bpmn" onChange={handleUpload} style={{ display: "none" }} />
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
        <button
          onClick={handleExport}
          disabled={!xml}
          className="toolbar-btn"
          title="Export current diagram as .bpmn file"
        >
          <DownloadIcon /> Export .bpmn
        </button>
        <button
          onClick={() => setDarkMode((d) => !d)}
          className="toolbar-btn toolbar-btn--icon"
          title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
        >
          {darkMode ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>

      {/* main area */}
      <div className="main-area">
        {/* left sidebar */}
        <div className="sidebar" style={{ width: sidebar.size }}>

          <div className="panel-section" style={{ height: validationSplit.size }}>
            <ValidationPanel
              issues={validationResult?.issues ?? []}
              semanticIssues={validationResult?.semantic_issues ?? []}
              isValid={validationResult?.is_valid}
              loading={validating}
              errorCount={errorCount}
            />
          </div>

          <div
            className={`resize-handle-v${validationSplit.isDragging ? " dragging" : ""}`}
            onMouseDown={validationSplit.handleMouseDown}
          />

          <div className="panel-section" style={{ height: chatSplit.size }}>
            <ChatPanel
              ir={diagram}
              issues={allIssues}
              sessionId={sessionId}
              onIrUpdate={handleChatUpdate}
              SendIcon={SendIcon}
            />
          </div>

          <div
            className={`resize-handle-v${chatSplit.isDragging ? " dragging" : ""}`}
            onMouseDown={chatSplit.handleMouseDown}
          />

          <div className="panel-section" style={{ flex: 1 }}>
            <HistoryPanel
              sessionId={sessionId}
              currentRevId={currentRevId}
              currentDiagram={diagram}
              editorRef={editorRef}
              previewRevId={previewRev?.rev_id ?? null}
              onPreview={handlePreview}
              onRevert={handleRevert}
            />
          </div>
        </div>

        {/* horizontal resize handle */}
        <div
          className={`resize-handle-h${sidebar.isDragging ? " dragging" : ""}`}
          onMouseDown={sidebar.handleMouseDown}
        />

        {/* editor pane — preview banner + canvas */}
        <div className="editor-pane" style={{ display: "flex", flexDirection: "column" }}>
          {previewRev && (
            <div className="preview-banner">
              <span>Previewing <strong>revision #{previewRev.index}</strong> — {previewRev.message}</span>
              <div className="preview-banner-spacer" />
              <button className="preview-banner-btn preview-banner-btn--restore" onClick={restorePreview}>
                Restore this version
              </button>
              <button className="preview-banner-btn preview-banner-btn--dismiss" onClick={dismissPreview}>
                Back to current
              </button>
            </div>
          )}
          <div style={{ flex: 1 }}>
            <BpmnEditor ref={editorRef} xml={xml} onXmlChange={handleXmlChange} />
          </div>
        </div>
      </div>
    </div>
  );
}
