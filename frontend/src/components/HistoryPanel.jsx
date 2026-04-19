import { useState, useEffect, useCallback } from "react";
import { layoutProcess } from "bpmn-auto-layout";
import { getHistory, getRevision, revertToRevision, exportDiagram } from "../api/client.js";
import { diffDiagrams } from "../utils/irDiff.js";

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

export default function HistoryPanel({
  sessionId,
  currentRevId,
  currentDiagram,
  editorRef,
  previewRevId,
  onPreview,
  onRevert,
}) {
  const [revisions, setRevisions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [reverting, setReverting] = useState(null);
  const [hoveredDiff, setHoveredDiff] = useState(null);

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

  async function handleMouseEnter(rev) {
    // don't apply hover diff while previewing — canvas shows a different diagram
    if (previewRevId || !editorRef?.current || !currentDiagram) return;
    try {
      const full = await getRevision(sessionId, rev.rev_id);
      const { added, modified, removed } = diffDiagrams(full.diagram, currentDiagram);
      editorRef.current.applyDiff({ added, modified });
      setHoveredDiff({ revId: rev.rev_id, removed });
    } catch (err) {
      console.error("diff failed:", err);
    }
  }

  function handleMouseLeave() {
    if (previewRevId) return;
    editorRef?.current?.clearDiff();
    setHoveredDiff(null);
  }

  async function handleClick(rev) {
    if (rev.rev_id === currentRevId) return;
    // clear any hover diff before loading preview
    editorRef?.current?.clearDiff();
    setHoveredDiff(null);
    try {
      const full = await getRevision(sessionId, rev.rev_id);
      const laid = await exportAndLayout(full.diagram);
      editorRef?.current?.importXml(laid);
      onPreview?.({ rev_id: rev.rev_id, diagram: full.diagram, message: rev.message, index: rev.index });
    } catch (err) {
      console.error("preview failed:", err);
    }
  }

  async function handleRevert(revId) {
    setReverting(revId);
    handleMouseLeave();
    try {
      const res = await revertToRevision(sessionId, revId);
      onRevert?.(res.diagram, res.new_rev_id);
      await refresh();
    } catch (err) {
      console.error("revert failed:", err);
    } finally {
      setReverting(null);
    }
  }

  return (
    <>
      <div className="panel-header">
        History
        {revisions.length > 0 && (
          <span className="badge">{revisions.length}</span>
        )}
        <button
          onClick={refresh}
          className="history-refresh-btn"
          title="Refresh history"
          style={{ marginLeft: "auto" }}
        >
          ↻
        </button>
      </div>

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
          const isInitial = i === revisions.length - 1;
          const isHovered = hoveredDiff?.revId === rev.rev_id;
          const removedList = isHovered ? hoveredDiff.removed : [];

          return (
            <div
              key={rev.rev_id}
              className={`history-card${isCurrent ? " history-card--current" : ""}${isPreviewing ? " history-card--preview" : ""}`}
              onMouseEnter={() => !isCurrent && handleMouseEnter(rev)}
              onMouseLeave={handleMouseLeave}
              onClick={() => !isCurrent && handleClick(rev)}
              style={{ cursor: isCurrent ? "default" : "pointer" }}
            >
              <div className="history-card-header">
                <span className={`history-author history-author--${rev.author}`}>
                  {rev.author === "llm" ? "AI" : "you"}
                </span>
                <span className="history-time">{formatTime(rev.timestamp)}</span>
                <span className="issue-rule">#{rev.index}</span>
                {isPreviewing && <span className="history-preview-badge">previewing</span>}
              </div>
              <p className="history-message">{rev.message}</p>

              {removedList.length > 0 && (
                <div className="diff-removed-list">
                  <span className="diff-removed-label">removed:</span>
                  {removedList.map((el) => (
                    <span key={el.id} className="diff-removed-item">
                      {el.name || el.id}
                    </span>
                  ))}
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
    </>
  );
}
