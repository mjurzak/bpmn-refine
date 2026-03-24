import React, { useState, useRef, useEffect } from "react";
import { sendChatMessage } from "../api/client.js";

export default function ChatPanel({ ir, issues, onIrUpdate, SendIcon }) {
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
            <div className={`chat-bubble chat-bubble--${m.role}`}>
              {m.content}
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
