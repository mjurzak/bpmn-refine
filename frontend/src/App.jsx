import React, { useState, useCallback, useRef, useEffect } from "react";
import { layoutProcess } from "bpmn-auto-layout";
import BpmnEditor from "./components/BpmnEditor.jsx";
import ValidationPanel from "./components/ValidationPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import HistoryPanel from "./components/HistoryPanel.jsx";
import {
  uploadDiagram,
  validateDiagram,
  exportDiagram,
  repairDiagram,
  applyEditOps,
  commitRevision,
} from "./api/client.js";
import { summarizeDiagramDiff } from "./utils/irDiff.js";
import useResize from "./hooks/useResize.js";
import "./app.css";

const UI_SESSION_KEYS = {
  darkMode: "bpmn-ai-validator.dark-mode",
  chatOpen: "bpmn-ai-validator.chat-open",
  leftPanelOpen: "bpmn-ai-validator.left-panel-open",
  historyOpen: "bpmn-ai-validator.history-open",
  approvalMode: "bpmn-ai-validator.approval-mode",
};

const APPROVAL_MODES = {
  MANUAL: "manual",
  AUTO: "autoapprove",
};

const VALIDATION_MODES = {
  STRUCTURAL: "structural",
  DEEP: "deep",
  SEMANTIC: "semantic",
};

const DEEP_VALIDATE_CONFIG = {
  tiers_enabled: { t1: true, t2: true, t3: false },
  t2_tools: ["woflan"],
};

const VALIDATION_LOADING_LABELS = {
  [VALIDATION_MODES.STRUCTURAL]: "Running structural validation...",
  [VALIDATION_MODES.DEEP]: "Running deep formal validation...",
  [VALIDATION_MODES.SEMANTIC]: "Running semantic LLM validation...",
};

function readSessionBoolean(key, fallback) {
  try {
    const value = window.sessionStorage.getItem(key);
    if (value === "true") return true;
    if (value === "false") return false;
  } catch {
    return fallback;
  }
  return fallback;
}

function writeSessionBoolean(key, value) {
  try {
    window.sessionStorage.setItem(key, String(value));
  } catch {
    // storage can be unavailable in private or locked-down browser sessions
  }
}

function readSessionString(key, fallback, allowedValues) {
  try {
    const value = window.sessionStorage.getItem(key);
    if (!allowedValues || allowedValues.includes(value)) return value ?? fallback;
  } catch {
    return fallback;
  }
  return fallback;
}

function writeSessionString(key, value) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // storage can be unavailable in private or locked-down browser sessions
  }
}

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

function UploadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 3 14h8l-1 8 10-12h-8l1-8z" />
    </svg>
  );
}

function RepairIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.8 2.8-3-3 2.8-2.8z" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" />
    </svg>
  );
}

function SidePanelIcon({ side = "right" }) {
  const dividerX = side === "left" ? 8 : 16;
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1={dividerX} y1="4" x2={dividerX} y2="20" />
    </svg>
  );
}

function proposalOpElementIds(op) {
  if (!op) return [];
  switch (op.op) {
    case "add_node":
    case "remove_node":
    case "rename_element":
    case "change_node_type":
    case "change_gateway_type":
      return [op.id];
    case "add_flow":
      return [op.source_ref, op.target_ref].filter(Boolean);
    case "remove_flow":
      return [op.id];
    case "set_condition":
      return [op.flow_id];
    default:
      return [];
  }
}

export default function App() {
  const [xml, setXml] = useState(null);
  const [diagram, setDiagram] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [currentRevId, setCurrentRevId] = useState(null);
  const [previewRev, setPreviewRev] = useState(null); // { rev_id, diagram, message, index }
  const [validationResult, setValidationResult] = useState(null);
  const [validating, setValidating] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [validationMode, setValidationMode] = useState(null);
  const [pendingProposal, setPendingProposal] = useState(null);
  const [darkMode, setDarkMode] = useState(() => readSessionBoolean(UI_SESSION_KEYS.darkMode, false));
  const [chatOpen, setChatOpen] = useState(() => readSessionBoolean(UI_SESSION_KEYS.chatOpen, true));
  const [leftPanelOpen, setLeftPanelOpen] = useState(() => readSessionBoolean(UI_SESSION_KEYS.leftPanelOpen, true));
  const [historyOpen, setHistoryOpen] = useState(() => readSessionBoolean(UI_SESSION_KEYS.historyOpen, true));
  const [approvalMode, setApprovalMode] = useState(() => readSessionString(
    UI_SESSION_KEYS.approvalMode,
    APPROVAL_MODES.MANUAL,
    Object.values(APPROVAL_MODES),
  ));

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", darkMode ? "dark" : "light");
    writeSessionBoolean(UI_SESSION_KEYS.darkMode, darkMode);
  }, [darkMode]);

  useEffect(() => {
    writeSessionBoolean(UI_SESSION_KEYS.chatOpen, chatOpen);
  }, [chatOpen]);

  useEffect(() => {
    writeSessionBoolean(UI_SESSION_KEYS.leftPanelOpen, leftPanelOpen);
  }, [leftPanelOpen]);

  useEffect(() => {
    writeSessionBoolean(UI_SESSION_KEYS.historyOpen, historyOpen);
  }, [historyOpen]);

  useEffect(() => {
    writeSessionString(UI_SESSION_KEYS.approvalMode, approvalMode);
  }, [approvalMode]);

  const editorRef = useRef(null);

  const sidebar = useResize({ initial: 340, min: 260, max: 520, axis: "horizontal" });
  const validationSplit = useResize({ initial: 220, min: 60, max: 500, axis: "vertical" });
  const assistantPanel = useResize({ initial: 360, min: 280, max: 560, axis: "horizontal", direction: -1 });

  const handleXmlChange = useCallback((updatedXml) => {
    // ignore canvas changes while previewing a historical snapshot
    if (previewRev) return;
    if (pendingProposal) setPendingProposal(null);
    setXml(updatedXml);
  }, [previewRev, pendingProposal]);

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
      setPendingProposal(null);
      const exported = await exportDiagram(res.diagram);
      const laid = await autoLayout(exported.xml);
      setXml(laid);
    } catch (err) {
      alert(`Upload failed: ${err.message}`);
    }
  }

  async function handleValidate(mode = VALIDATION_MODES.STRUCTURAL) {
    if (!diagram) return alert("Upload a diagram first.");
    const includeSemanticPass = mode === VALIDATION_MODES.SEMANTIC;
    const config = mode === VALIDATION_MODES.DEEP ? DEEP_VALIDATE_CONFIG : {};
    setValidating(true);
    setValidationMode(mode);
    try {
      const res = await validateDiagram(diagram, includeSemanticPass, config);
      setValidationResult(res);
    } catch (err) {
      alert(`Validation failed: ${err.message}`);
    } finally {
      setValidating(false);
      setValidationMode(null);
    }
  }

  async function handleChatUpdate(updatedDiagram, newRevId, newSessionId) {
    dismissPreview();
    await applyDiagram(updatedDiagram);
    if (newSessionId) setSessionId(newSessionId);
    if (newRevId) setCurrentRevId(newRevId);
  }

  function handleDiagramProposal({ source, diagram: proposedDiagram, baseDiagram = diagram, message, remainingIssues = null, ops = [] }) {
    if (!baseDiagram || !proposedDiagram) return;
    setPendingProposal({
      source,
      baseDiagram,
      diagram: proposedDiagram,
      message,
      remainingIssues,
      ops,
      diff: summarizeDiagramDiff(baseDiagram, proposedDiagram),
    });
    setChatOpen(true);
  }

  async function applyAcceptedProposal(selectedOps = null) {
    if (!pendingProposal) return;
    try {
      let acceptedDiagram = pendingProposal.diagram;
      let acceptedRemainingIssues = pendingProposal.remainingIssues;
      if (pendingProposal.ops?.length > 0 && selectedOps !== null) {
        if (selectedOps.length === 0) {
          alert("Select at least one operation to apply.");
          return;
        }
        const applied = await applyEditOps(pendingProposal.baseDiagram, selectedOps);
        const failed = applied.op_results.filter((result) => !result.applied);
        if (failed.length > 0) {
          alert(`Could not apply selected operation: ${failed[0].error ?? "unknown error"}`);
          return;
        }
        acceptedDiagram = applied.updated_diagram;
        if (selectedOps.length !== pendingProposal.ops.length) {
          acceptedRemainingIssues = null;
        }
      }
      const commit = await commitRevision(
        acceptedDiagram,
        sessionId,
        pendingProposal.message,
        "llm",
      );
      dismissPreview();
      await applyDiagram(commit.diagram);
      setSessionId(commit.session_id);
      setCurrentRevId(commit.new_rev_id);
      if (pendingProposal.source === "repair" && acceptedRemainingIssues) {
        setValidationResult({
          is_valid: acceptedRemainingIssues.length === 0,
          issues: acceptedRemainingIssues,
          semantic_issues: [],
        });
      } else {
        setValidationResult(null);
      }
      editorRef.current?.clearDiff();
      setPendingProposal(null);
    } catch (err) {
      alert(`Applying proposal failed: ${err.message}`);
    }
  }

  function rejectProposal() {
    editorRef.current?.clearDiff();
    setPendingProposal(null);
  }

  function focusProposalOperation(op) {
    if (!op) {
      editorRef.current?.clearDiff();
      return;
    }
    const ids = proposalOpElementIds(op);
    editorRef.current?.highlightElements(ids);
  }

  async function handleRevert(revertedDiagram, newRevId) {
    dismissPreview();
    await applyDiagram(revertedDiagram);
    setDiagram(revertedDiagram);
    setCurrentRevId(newRevId);
    setValidationResult(null);
    setPendingProposal(null);
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

  async function handleRepair() {
    if (!xml) return alert("Upload a diagram first.");
    if (allIssues.length === 0) return alert("Run validation first and select a diagram with issues.");
    setRepairing(true);
    try {
      const res = await repairDiagram(xml, allIssues);
      const proposedDiagram = res.updated_diagram;
      if (approvalMode === APPROVAL_MODES.AUTO) {
        const commit = await commitRevision(
          proposedDiagram,
          sessionId,
          "auto-approved repair proposal",
          "llm",
        );
        dismissPreview();
        await applyDiagram(commit.diagram);
        setSessionId(commit.session_id);
        setCurrentRevId(commit.new_rev_id);
        setValidationResult({
          is_valid: res.remaining_issues.length === 0,
          issues: res.remaining_issues,
          semantic_issues: [],
        });
        setPendingProposal(null);
      } else {
        handleDiagramProposal({
          source: "repair",
          baseDiagram: res.input_diagram,
          diagram: proposedDiagram,
          message: "accepted repair proposal",
          remainingIssues: res.remaining_issues,
          ops: res.applied_ops,
        });
      }
    } catch (err) {
      alert(`Repair failed: ${err.message}`);
    } finally {
      setRepairing(false);
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
        <div className="toolbar-caption">interactive BPMN validation and refinement</div>
        <div className="toolbar-spacer" />
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
        <div className={`sidebar${!leftPanelOpen ? " sidebar--hidden" : ""}`} style={leftPanelOpen ? { width: sidebar.size } : undefined}>
          <div className="sidebar-header">
            <span>Diagram panels</span>
            <button className="panel-toggle-btn" onClick={() => setLeftPanelOpen(false)} title="Hide left panel" aria-label="Hide left panel">
              <SidePanelIcon side="left" />
            </button>
          </div>

          <div className="panel-section" style={historyOpen ? { height: validationSplit.size } : { flex: 1 }}>
            <ValidationPanel
              issues={validationResult?.issues ?? []}
              semanticIssues={validationResult?.semantic_issues ?? []}
              isValid={validationResult?.is_valid}
              loading={validating}
              loadingLabel={VALIDATION_LOADING_LABELS[validationMode] ?? "Running validation..."}
              errorCount={errorCount}
            />
          </div>

          {historyOpen && (
            <div className={`resize-handle-v${validationSplit.isDragging ? " dragging" : ""}`} onMouseDown={validationSplit.handleMouseDown} />
          )}

          <div className="panel-section" style={historyOpen ? { flex: 1 } : { flex: "0 0 auto", minHeight: 0 }}>
            <HistoryPanel
              sessionId={sessionId}
              currentRevId={currentRevId}
              viewDiagram={previewRev?.diagram ?? diagram}
              editorRef={editorRef}
              previewRevId={previewRev?.rev_id ?? null}
              onPreview={handlePreview}
              onRevert={handleRevert}
              onDismissPreview={dismissPreview}
              collapsed={!historyOpen}
              onToggleCollapse={() => setHistoryOpen((open) => !open)}
            />
          </div>
        </div>

        {!leftPanelOpen && (
          <div className="left-panel-rail">
            <button className="left-panel-rail-btn" onClick={() => setLeftPanelOpen(true)} title="Show validation and history panels">
              <SidePanelIcon side="left" />
              <span>Panels</span>
            </button>
          </div>
        )}

        {/* horizontal resize handle */}
        {leftPanelOpen && (
          <div
            className={`resize-handle-h${sidebar.isDragging ? " dragging" : ""}`}
            onMouseDown={sidebar.handleMouseDown}
          />
        )}

        {/* editor pane — canvas actions and BPMN modeler */}
        <div className="editor-pane">
          <div className="diagram-action-bar">
            <div className="diagram-action-group diagram-action-group--file">
              <label className="diagram-action diagram-action--primary">
                <UploadIcon /> Import BPMN
                <input type="file" accept=".bpmn" onChange={handleUpload} style={{ display: "none" }} />
              </label>
              <button
                onClick={handleExport}
                disabled={!xml}
                className="diagram-action diagram-action--secondary"
                title="Export current diagram as .bpmn file"
              >
                <DownloadIcon /> Export
              </button>
            </div>
            <div className="diagram-action-spacer" />
            <div className="diagram-action-group diagram-action-group--validation">
              <button
                onClick={() => handleValidate(VALIDATION_MODES.STRUCTURAL)}
                disabled={!diagram || validating}
                className="diagram-action diagram-action--validate"
                title="Run deterministic structural validation"
              >
                <CheckIcon /> Validate
              </button>
              <button
                onClick={() => handleValidate(VALIDATION_MODES.DEEP)}
                disabled={!diagram || validating}
                className="diagram-action diagram-action--deep"
                title="Run tier-1 rules plus PM4Py Woflan formal validation"
              >
                <SparkIcon /> Deep Validate
              </button>
              <button
                onClick={() => handleValidate(VALIDATION_MODES.SEMANTIC)}
                disabled={!diagram || validating}
                className="diagram-action diagram-action--ai"
                title="Run structural validation plus LLM semantic analysis"
              >
                <SparkIcon /> Semantic LLM
              </button>
              <button
                onClick={handleRepair}
                disabled={!xml || repairing || allIssues.length === 0}
                className="diagram-action diagram-action--repair"
                title="Propose repairs for current validation issues"
              >
                <RepairIcon /> {repairing ? "Repairing..." : "Repair"}
              </button>
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <BpmnEditor ref={editorRef} xml={xml} onXmlChange={handleXmlChange} />
          </div>
        </div>

        <>
          {chatOpen && (
            <div
              className={`resize-handle-h resize-handle-h--right${assistantPanel.isDragging ? " dragging" : ""}`}
              onMouseDown={assistantPanel.handleMouseDown}
            />
          )}
          <aside className={`assistant-panel${!chatOpen ? " assistant-panel--hidden" : ""}`} style={chatOpen ? { width: assistantPanel.size } : undefined}>
            <div className="assistant-panel-header">
              <div>
                <div className="assistant-panel-title"><ChatIcon /> AI diagram assistant</div>
                <div className="assistant-panel-subtitle">Ask for BPMN improvements or refinements</div>
              </div>
              <button className="panel-toggle-btn" onClick={() => setChatOpen(false)} title="Hide AI chat" aria-label="Hide AI chat">
                <SidePanelIcon side="right" />
              </button>
            </div>
            <ChatPanel
              ir={diagram}
              issues={allIssues}
              sessionId={sessionId}
              onIrUpdate={handleChatUpdate}
              onDiagramProposal={handleDiagramProposal}
              SendIcon={SendIcon}
              showHeader={false}
              approvalMode={approvalMode}
              onApprovalModeChange={setApprovalMode}
              pendingProposal={pendingProposal}
              onApplyProposal={applyAcceptedProposal}
              onRejectProposal={rejectProposal}
              onFocusProposalOp={focusProposalOperation}
            />
          </aside>
        </>

        {!chatOpen && (
          <div className="assistant-rail">
            <button className="assistant-rail-btn" onClick={() => setChatOpen(true)} title="Show AI diagram assistant">
              <ChatIcon />
              <span>AI Chat</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
