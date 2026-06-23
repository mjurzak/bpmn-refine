import { useMemo, useState } from "react";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "action", label: "Actions" },
  { key: "llm", label: "LLM" },
  { key: "error", label: "Errors" },
];

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(value) {
  if (typeof value !== "number") return null;
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`;
}

function prettyJson(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function traceInput(trace) {
  if (trace.messages) return prettyJson(trace.messages);
  return trace.prompt ?? "";
}

function LogBlock({ label, value }) {
  const text = prettyJson(value);
  if (!text) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // clipboard may be unavailable in non-secure browser contexts
    }
  }

  return (
    <div className="log-detail-block">
      <div className="log-detail-header">
        <span>{label}</span>
        <button type="button" onClick={copy}>Copy</button>
      </div>
      <pre>{text}</pre>
    </div>
  );
}

export default function LogsPanel({ entries = [], onClear }) {
  const [filter, setFilter] = useState("all");
  const [expandedId, setExpandedId] = useState(null);

  const visibleEntries = useMemo(() => {
    if (filter === "all") return entries;
    if (filter === "error") return entries.filter((entry) => entry.status === "error");
    return entries.filter((entry) => entry.type === filter);
  }, [entries, filter]);

  return (
    <div className="logs-panel">
      <div className="logs-toolbar">
        <div className="logs-filter" role="group" aria-label="Log filters">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              className={`logs-filter-btn${filter === item.key ? " logs-filter-btn--active" : ""}`}
              type="button"
              onClick={() => setFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <button className="logs-clear-btn" type="button" onClick={onClear} disabled={entries.length === 0}>
          Clear
        </button>
      </div>

      <div className="logs-body">
        {visibleEntries.length === 0 && (
          <div className="validation-empty">No log entries yet.</div>
        )}

        {visibleEntries.map((entry, index) => {
          const isExpanded = expandedId === entry.id;
          const duration = formatDuration(entry.durationMs);
          const trace = entry.trace;
          return (
            <article
              key={entry.id}
              className={`log-entry log-entry--${entry.type} log-entry--${entry.status ?? "info"}`}
            >
              <button
                type="button"
                className="log-entry-summary"
                onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                aria-expanded={isExpanded}
              >
                <span className="log-step">{index + 1}</span>
                <span className="log-main">
                  <span className="log-title-row">
                    <span className="log-title">{entry.title}</span>
                    <span className="log-meta">{formatTime(entry.timestamp)}</span>
                  </span>
                  {entry.summary && <span className="log-summary">{entry.summary}</span>}
                </span>
                {duration && <span className="log-duration">{duration}</span>}
              </button>

              {isExpanded && (
                <div className="log-entry-details">
                  {entry.details && <LogBlock label="Details" value={entry.details} />}
                  {trace && (
                    <>
                      <div className="log-trace-meta">
                        <span>{trace.provider ?? "default provider"}</span>
                        <span>{trace.model}</span>
                        <span>{trace.kind}</span>
                      </div>
                      <LogBlock label="System" value={trace.system} />
                      <LogBlock label="Input" value={traceInput(trace)} />
                      <LogBlock label="Schema" value={trace.schema} />
                      <LogBlock label="Output" value={trace.output} />
                      <LogBlock label="Error" value={trace.error} />
                    </>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
