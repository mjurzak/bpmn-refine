import { useState, useEffect, useCallback, useRef } from "react";
import { layoutProcess } from "bpmn-auto-layout";
import { getHistory, getRevision, revertToRevision, exportDiagram } from "../api/client.js";
import { diffDiagrams, summarizeDiagramDiff } from "../utils/irDiff.js";
import DiffBlock from "./DiffBlock.jsx";

async function exportAndLayout(diagram) {
  const { xml } = await exportDiagram(diagram);
  try {
    return await layoutProcess(xml);
  } catch {
    return xml;
  }
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function historyMessage(rev) {
  if (rev.author === "llm") return "AI diagram update";
  return rev.message;
}

function CaretIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

export default function HistoryPanel({
  sessionId,
  currentRevId,
  viewDiagram,
  editorRef,
  previewRevId,
  onPreview,
  onRevert,
  onDismissPreview,
  collapsed = false,
  onToggleCollapse,
}) {
  const [revisions, setRevisions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [reverting, setReverting] = useState(null);
  const [hoveredDiff, setHoveredDiff] = useState(null);
  const [expandedDiff, setExpandedDiff] = useState(null);
  const hoverRequestRef = useRef(0);
  const revisionCacheRef = useRef(new Map());

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await getHistory(sessionId);
      setRevisions([...res.revisions].reverse());
    } catch (err) {
      console.error("failed to load history:", err);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    refresh();
  }, [refresh, currentRevId]);

  useEffect(() => {
    revisionCacheRef.current.clear();
    setHoveredDiff(null);
    setExpandedDiff(null);
  }, [sessionId]);

  const activeViewRevId = previewRevId ?? currentRevId;

  useEffect(() => {
    hoverRequestRef.current += 1;
    editorRef?.current?.clearDiff();
    setHoveredDiff(null);
  }, [activeViewRevId, editorRef]);

  async function handleMouseEnter(rev) {
    if (rev.rev_id === activeViewRevId || !editorRef?.current || !viewDiagram) return;
    const requestId = hoverRequestRef.current + 1;
    hoverRequestRef.current = requestId;
    try {
      const full = await loadRevision(rev.rev_id);
      if (hoverRequestRef.current !== requestId) return;
      const { added, modified } = diffDiagrams(full.diagram, viewDiagram);
      editorRef.current.clearDiff();
      editorRef.current.applyDiff({ added, modified });
      setHoveredDiff({
        revId: rev.rev_id,
        content: summarizeDiagramDiff(full.diagram, viewDiagram),
      });
    } catch (err) {
      console.error("diff failed:", err);
    }
  }

  function handleMouseLeave() {
    hoverRequestRef.current += 1;
    editorRef?.current?.clearDiff();
    setHoveredDiff(null);
  }

  async function handleClick(rev) {
    if (rev.rev_id === activeViewRevId) {
      if (expandedDiff?.revId === rev.rev_id) {
        setExpandedDiff(null);
      } else {
        await expandRevisionDiff(rev);
      }
      return;
    }
    if (previewRevId && rev.rev_id === currentRevId) {
      handleMouseLeave();
      onDismissPreview?.();
      return;
    }
    // clear any hover diff before loading preview
    editorRef?.current?.clearDiff();
    setHoveredDiff(null);
    try {
      const full = await loadRevision(rev.rev_id);
      const laid = await exportAndLayout(full.diagram);
      editorRef?.current?.importXml(laid);
      onPreview?.({ rev_id: rev.rev_id, diagram: full.diagram, message: rev.message, index: rev.index });
      await expandRevisionDiff(rev, full);
    } catch (err) {
      console.error("preview failed:", err);
    }
  }

  async function loadRevision(revId) {
    const cached = revisionCacheRef.current.get(revId);
    if (cached) return cached;
    const full = await getRevision(sessionId, revId);
    revisionCacheRef.current.set(revId, full);
    return full;
  }

  async function expandRevisionDiff(rev, fullRevision = null) {
    try {
      const current = fullRevision ?? await loadRevision(rev.rev_id);
      const previousMeta = revisions.find((item) => item.index === rev.index - 1);
      const content = previousMeta
        ? summarizeDiagramDiff((await loadRevision(previousMeta.rev_id)).diagram, current.diagram)
        : "  initial uploaded diagram";
      setExpandedDiff({ revId: rev.rev_id, content });
    } catch (err) {
      console.error("revision diff failed:", err);
    }
  }

  async function handleRevert(revId) {
    setReverting(revId);
    handleMouseLeave();
    try {
      const res = await revertToRevision(sessionId, revId);
      await onRevert?.(res.diagram, res.new_rev_id);
      await refresh();
    } catch (err) {
      console.error("revert failed:", err);
      alert(`Revert failed: ${err.message}`);
    } finally {
      setReverting(null);
    }
  }

  function handleDismissPreview() {
    handleMouseLeave();
    onDismissPreview?.();
  }

  return (
    <>
      <div
        className={`panel-header panel-header--collapsible${collapsed ? " panel-header--collapsed" : ""}`}
        onClick={onToggleCollapse}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggleCollapse?.();
          }
        }}
        title={collapsed ? "Expand history" : "Collapse history"}
      >
        <span className="panel-collapse-caret"><CaretIcon /></span>
        History
        {revisions.length > 0 && (
          <span className="badge">{revisions.length}</span>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); refresh(); }}
          className="history-refresh-btn"
          title="Refresh history"
          style={{ marginLeft: "auto" }}
        >
          ↻
        </button>
      </div>

      {!collapsed && (
      <div className="validation-body">
        {!sessionId && (
          <div className="validation-empty">Upload a diagram to start tracking changes.</div>
        )}
        {sessionId && loading && revisions.length === 0 && (
          <div className="validation-empty">Loading...</div>
        )}
        {sessionId && !loading && revisions.length === 0 && (
          <div className="validation-empty">No revisions yet.</div>
        )}

        {revisions.map((rev, i) => {
          const isCurrent = rev.rev_id === currentRevId;
          const isPreviewing = rev.rev_id === previewRevId;
          const isViewHead = rev.rev_id === activeViewRevId;
          const isInitial = i === revisions.length - 1;
          const isHovered = hoveredDiff?.revId === rev.rev_id;
          const cardDiff = expandedDiff?.revId === rev.rev_id
            ? { label: "changes in this revision", content: expandedDiff.content }
            : isHovered
              ? { label: "diff to current view", content: hoveredDiff.content }
              : null;

          return (
            <div
              key={rev.rev_id}
              className={`history-card${isCurrent ? " history-card--current" : ""}${isPreviewing ? " history-card--preview" : ""}`}
              onMouseEnter={() => !isViewHead && handleMouseEnter(rev)}
              onMouseLeave={handleMouseLeave}
              onClick={() => handleClick(rev)}
              style={{ cursor: "pointer" }}
            >
              <div className="history-card-header">
                <span className={`history-author history-author--${rev.author}`}>
                  {rev.author === "llm" ? "AI" : "you"}
                </span>
                <span className="history-time">{formatTime(rev.timestamp)}</span>
                <span className="issue-rule">#{rev.index}</span>
                {isPreviewing && <span className="history-preview-badge">previewing</span>}
              </div>
              {!cardDiff && <p className="history-message">{historyMessage(rev)}</p>}

              {isPreviewing && (
                <div className="history-preview-actions">
                  <button
                    className="history-restore-btn"
                    onClick={(e) => { e.stopPropagation(); handleRevert(rev.rev_id); }}
                    disabled={reverting === rev.rev_id}
                  >
                    {reverting === rev.rev_id ? "Restoring..." : "Restore this version"}
                  </button>
                  <button
                    className="history-current-btn"
                    onClick={(e) => { e.stopPropagation(); handleDismissPreview(); }}
                  >
                    Back to current
                  </button>
                </div>
              )}

              {cardDiff && (
                <div className="history-diff-panel">
                  <div className="history-diff-label">{cardDiff.label}</div>
                  <DiffBlock content={cardDiff.content} />
                </div>
              )}

              {!isCurrent && !isPreviewing && !isInitial && (
                <button
                  className="history-revert-btn"
                  onClick={(e) => { e.stopPropagation(); handleRevert(rev.rev_id); }}
                  disabled={reverting === rev.rev_id}
                >
                  {reverting === rev.rev_id ? "Reverting..." : "Revert to this"}
                </button>
              )}
            </div>
          );
        })}
      </div>
      )}
    </>
  );
}
