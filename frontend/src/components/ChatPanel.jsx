import React, { useState, useRef, useEffect } from "react";
import { sendChatMessage } from "../api/client.js";

export default function ChatPanel({ ir, issues, onIrUpdate }) {
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

    const userMsg = { role: "user", content: text };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);

    try {
      const res = await sendChatMessage(nextMessages, ir, issues);
      const assistantMsg = { role: "assistant", content: res.reply };
      setMessages((prev) => [...prev, assistantMsg]);
      if (res.updated_diagram) {
        onIrUpdate?.(res.updated_diagram);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${err.message}` },
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
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <h3 style={{ padding: "12px 12px 4px" }}>Chat</h3>

      <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px" }}>
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              marginBottom: "10px",
              textAlign: m.role === "user" ? "right" : "left",
            }}
          >
            <span
              style={{
                display: "inline-block",
                maxWidth: "85%",
                padding: "8px 12px",
                borderRadius: "12px",
                background: m.role === "user" ? "#1976d2" : "#f0f0f0",
                color: m.role === "user" ? "#fff" : "#333",
                whiteSpace: "pre-wrap",
                fontSize: "0.9em",
              }}
            >
              {m.content}
            </span>
          </div>
        ))}
        {loading && (
          <div style={{ color: "#888", fontSize: "0.85em" }}>Thinking…</div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={{ display: "flex", padding: "8px 12px", borderTop: "1px solid #ddd" }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about the diagram or request changes…"
          rows={2}
          style={{
            flex: 1,
            resize: "none",
            padding: "8px",
            borderRadius: "4px",
            border: "1px solid #ccc",
            fontSize: "0.9em",
          }}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          style={{
            marginLeft: "8px",
            padding: "0 16px",
            background: "#1976d2",
            color: "#fff",
            border: "none",
            borderRadius: "4px",
            cursor: "pointer",
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}
