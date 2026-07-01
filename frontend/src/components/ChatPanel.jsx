import React, { useState, useRef, useEffect } from "react";
import { sendChatMessage } from "../api/client.js";
import DiffBlock from "./DiffBlock.jsx";

function buildAssistantMessage(reply, updatedDiagram) {
  if (!updatedDiagram) {
    return { content: reply, processed: false };
  }

  return {
    content: reply,
    processed: true,
  };
}

function stripDiagramPayloads(content) {
  const pattern = /```(?:diagram|ir|json)\s*[\s\S]*?```/g;
  const segments = [];
  let lastIndex = 0;
  let match;

  while ((match = pattern.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push(content.slice(lastIndex, match.index));
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    segments.push(content.slice(lastIndex));
  }

  return segments.join("\n").trim() || content;
}

function opLabel(op) {
  switch (op.op) {
    case "add_node":
      return `Add ${op.node_type} ${op.name ? `"${op.name}"` : op.id}`;
    case "remove_node":
      return `Remove node ${op.id}${op.cascade ? " and incident flows" : ""}`;
    case "add_flow":
      return `Add flow ${op.id}: ${op.source_ref} -> ${op.target_ref}`;
    case "remove_flow":
      return `Remove flow ${op.id}`;
    case "rename_element":
      return `Rename ${op.id} to "${op.new_name}"`;
    case "change_node_type":
      return `Change ${op.id} to ${op.new_type}`;
    case "change_gateway_type":
      return `Change gateway ${op.id} to ${op.new_type}`;
    case "set_condition":
      return op.condition_expression
        ? `Set condition on ${op.flow_id}`
        : `Clear condition on ${op.flow_id}`;
    case "replace_diagram":
      return "Replace full diagram";
    default:
      return op.op;
  }
}

function renderInlineMarkdown(text) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return parts.filter(Boolean).map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}

function MarkdownContent({ content }) {
  const lines = content.split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    if (!lines[index].trim()) {
      index += 1;
      continue;
    }

    if (lines[index].startsWith("## ")) {
      blocks.push({ type: "heading", text: lines[index].slice(3) });
      index += 1;
      continue;
    }

    if (lines[index].startsWith("- ")) {
      const items = [];
      while (index < lines.length && lines[index].startsWith("- ")) {
        items.push(lines[index].slice(2));
        index += 1;
      }
      blocks.push({ type: "list", items });
      continue;
    }

    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !lines[index].startsWith("## ") && !lines[index].startsWith("- ")) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join(" ") });
  }

  return (
    <div className="chat-markdown">
      {blocks.map((block, blockIndex) => {
        if (block.type === "heading") {
          return <h4 key={blockIndex}>{renderInlineMarkdown(block.text)}</h4>;
        }

        if (block.type === "list") {
          return (
            <ul key={blockIndex}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{renderInlineMarkdown(item)}</li>
              ))}
            </ul>
          );
        }

        return <p key={blockIndex}>{renderInlineMarkdown(block.text)}</p>;
      })}
    </div>
  );
}

export default function ChatPanel({
  ir,
  issues,
  sessionId,
  onIrUpdate,
  onDiagramProposal,
  SendIcon,
  showHeader = true,
  approvalMode = "manual",
  onApprovalModeChange,
  pendingProposal,
  onApplyProposal,
  onRejectProposal,
  onFocusProposalOp,
  config = {},
  onActivity,
  disabled = false,
  disabledReason = null,
}) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [selectedOpIndexes, setSelectedOpIndexes] = useState([]);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    setSelectedOpIndexes((pendingProposal?.ops ?? []).map((_, index) => index));
    onFocusProposalOp?.(null);
  }, [pendingProposal]);

  function toggleProposalOp(index) {
    setSelectedOpIndexes((current) => (
      current.includes(index)
        ? current.filter((item) => item !== index)
        : [...current, index].sort((a, b) => a - b)
    ));
  }

  function selectedProposalOps() {
    const ops = pendingProposal?.ops ?? [];
    return selectedOpIndexes.map((index) => ops[index]).filter(Boolean);
  }

  async function handleSend() {
    if (disabled) return;
    const text = input.trim();
    if (!text) return;

    const userMsg = { role: "user", content: text, processed: false };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);
    const autoApprove = approvalMode === "autoapprove";
    const startedAt = performance.now();

    try {
      const res = await sendChatMessage(
        nextMessages.map(({ role, content }) => ({ role, content })),
        ir,
        issues,
        sessionId,
        autoApprove,
        config,
      );
      console.debug("chat completion reply:", res.reply);
      const assistantMsg = {
        role: "assistant",
        ...buildAssistantMessage(res.reply, res.updated_diagram),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      if (res.updated_diagram) {
        if (autoApprove) {
          onIrUpdate?.(res.updated_diagram, res.rev_id ?? null, res.session_id ?? null);
        } else {
          onDiagramProposal?.({
            source: "chat",
            diagram: res.updated_diagram,
            message: "accepted chat proposal",
          });
        }
      }
      onActivity?.({
        title: "Chat turn",
        summary: res.updated_diagram ? "diagram proposal returned" : "text reply returned",
        startedAt,
        response: res,
        details: {
          messages: nextMessages.length,
          updated_diagram: Boolean(res.updated_diagram),
          approval_mode: approvalMode,
        },
      });
    } catch (err) {
      onActivity?.({
        title: "Chat turn",
        startedAt,
        error: err,
        details: {
          messages: nextMessages.length,
          approval_mode: approvalMode,
        },
      });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${err.message}`, processed: false },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <>
      {showHeader && <div className="panel-header">Chat</div>}

      <div className="approval-mode-bar">
        <div>
          <div className="approval-mode-title">Change approvals</div>
          <div className="approval-mode-subtitle">manual approval keeps model edits as proposals</div>
        </div>
        <div className="approval-mode-toggle" role="group" aria-label="Change approval mode">
          <button
            className={`approval-mode-option${approvalMode === "manual" ? " approval-mode-option--active" : ""}`}
            onClick={() => onApprovalModeChange?.("manual")}
            type="button"
          >
            Manual
          </button>
          <button
            className={`approval-mode-option${approvalMode === "autoapprove" ? " approval-mode-option--active" : ""}`}
            onClick={() => onApprovalModeChange?.("autoapprove")}
            type="button"
          >
            Auto
          </button>
        </div>
      </div>

      <div className="chat-body">
        {messages.length === 0 && !loading && (
          <div className="validation-empty" style={{ height: "auto", paddingTop: 40 }}>
            Ask the AI assistant to explain, repair, or refine the current BPMN diagram.
          </div>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            className={`chat-message chat-message--${m.role}`}
          >
            <div className={`chat-bubble chat-bubble--${m.role}${m.processed ? " chat-bubble--processed" : ""}`}>
              {m.processed && <div className="chat-bubble-label">diagram updated</div>}
              {m.role === "assistant" ? <MarkdownContent content={m.processed ? stripDiagramPayloads(m.content) : m.content} /> : m.content}
            </div>
          </div>
        ))}

        {loading && <div className="chat-thinking">Thinking...</div>}
        <div ref={bottomRef} />
      </div>

      {pendingProposal && (
        <div className="proposal-card">
          <div className="proposal-card-header">
            <div>
              <div className="proposal-card-title">
                {pendingProposal.source === "repair" ? "Repair proposal" : "Chat proposal"}
              </div>
              <div className="proposal-card-subtitle">review the diagram change before applying it</div>
            </div>
          </div>
          {pendingProposal.ops?.length > 0 && (
            <div className="proposal-op-list">
              {pendingProposal.ops.map((op, index) => {
                const selected = selectedOpIndexes.includes(index);
                return (
                  <label
                    key={`${op.op}-${index}`}
                    className={`proposal-op-row${selected ? " proposal-op-row--selected" : ""}`}
                    onMouseEnter={() => onFocusProposalOp?.(op)}
                    onMouseLeave={() => onFocusProposalOp?.(null)}
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggleProposalOp(index)}
                    />
                    <span className="proposal-op-index">{index + 1}</span>
                    <span>{opLabel(op)}</span>
                  </label>
                );
              })}
            </div>
          )}
          <DiffBlock content={pendingProposal.diff} />
          <div className="proposal-card-actions">
            <button
              className="proposal-action proposal-action--apply"
              onClick={() => onApplyProposal?.(pendingProposal.ops?.length > 0 ? selectedProposalOps() : null)}
              type="button"
            >
              {pendingProposal.ops?.length > 0 ? `Apply selected (${selectedOpIndexes.length})` : "Apply"}
            </button>
            <button className="proposal-action" onClick={onRejectProposal} type="button">
              Reject
            </button>
          </div>
        </div>
      )}

      <div className="chat-input-area">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled
            ? (disabledReason ?? "Chat is unavailable for this file.")
            : "Ask for an improvement, repair, or explanation..."}
          rows={2}
          className="chat-textarea"
          disabled={disabled}
        />
        <button
          onClick={handleSend}
          disabled={disabled || loading || !input.trim()}
          className="chat-send-btn"
          title="Send message"
        >
          {SendIcon ? <SendIcon /> : "Send"}
        </button>
      </div>
    </>
  );
}
