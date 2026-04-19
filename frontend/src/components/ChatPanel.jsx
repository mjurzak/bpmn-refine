import React, { useState, useRef, useEffect } from "react";
import { sendChatMessage } from "../api/client.js";
import { summarizeDiagramDiff } from "../utils/irDiff.js";

function buildAssistantMessage(reply, previousDiagram, updatedDiagram) {
  if (!updatedDiagram) {
    return { content: reply, processed: false };
  }

  return {
    content: reply,
    diff: summarizeDiagramDiff(previousDiagram, updatedDiagram),
    processed: true,
  };
}

function splitProcessedReply(content) {
  const pattern = /```(?:diagram|ir|json)\s*[\s\S]*?```/g;
  const segments = [];
  let lastIndex = 0;
  let match;

  while ((match = pattern.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: "markdown", content: content.slice(lastIndex, match.index) });
    }
    segments.push({ type: "diff" });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    segments.push({ type: "markdown", content: content.slice(lastIndex) });
  }

  return segments.length > 0 ? segments : [{ type: "markdown", content }];
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

function DiffContent({ content }) {
  return (
    <pre className="chat-diff-block">
      {content.split("\n").map((line, index) => {
        let className = "chat-diff-line";
        if (line.startsWith("+ ")) className += " chat-diff-line--added";
        else if (line.startsWith("- ")) className += " chat-diff-line--removed";
        else if (line.startsWith("~ ")) className += " chat-diff-line--modified";
        else className += " chat-diff-line--detail";

        return (
          <span key={index} className={className}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}

function ProcessedAssistantContent({ content, diff }) {
  const segments = splitProcessedReply(content);

  return (
    <div className="chat-processed-content">
      {segments.map((segment, index) => {
        if (segment.type === "diff") {
          return <DiffContent key={index} content={diff} />;
        }

        if (!segment.content.trim()) {
          return null;
        }

        return <MarkdownContent key={index} content={segment.content.trim()} />;
      })}
    </div>
  );
}

export default function ChatPanel({ ir, issues, sessionId, onIrUpdate, SendIcon }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const text = input.trim();
    if (!text) return;

    const userMsg = { role: "user", content: text, processed: false };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);

    try {
      const res = await sendChatMessage(
        nextMessages.map(({ role, content }) => ({ role, content })),
        ir,
        issues,
        sessionId,
      );
      console.debug("chat completion reply:", res.reply);
      const assistantMsg = {
        role: "assistant",
        ...buildAssistantMessage(res.reply, ir, res.updated_diagram),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      if (res.updated_diagram) {
        onIrUpdate?.(res.updated_diagram, res.rev_id ?? null, res.session_id ?? null);
      }
    } catch (err) {
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
      <div className="panel-header">Chat</div>

      <div className="chat-body">
        {messages.length === 0 && !loading && (
          <div className="validation-empty" style={{ height: "auto", paddingTop: 40 }}>
            Ask questions about your diagram or request changes.
          </div>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            className={`chat-message chat-message--${m.role}`}
          >
            <div className={`chat-bubble chat-bubble--${m.role}${m.processed ? " chat-bubble--processed" : ""}`}>
              {m.processed && <div className="chat-bubble-label">processed diff</div>}
              {m.processed ? <ProcessedAssistantContent content={m.content} diff={m.diff} /> : m.role === "assistant" ? <MarkdownContent content={m.content} /> : m.content}
            </div>
          </div>
        ))}

        {loading && <div className="chat-thinking">Thinking...</div>}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-area">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about the diagram..."
          rows={2}
          className="chat-textarea"
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          className="chat-send-btn"
          title="Send message"
        >
          {SendIcon ? <SendIcon /> : "Send"}
        </button>
      </div>
    </>
  );
}
