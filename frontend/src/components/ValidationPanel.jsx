import React from "react";

const SEVERITY_COLOR = { error: "#d32f2f", warning: "#f57c00", info: "#0288d1" };

export default function ValidationPanel({ issues = [], semanticIssues = [], isValid, loading }) {
  const all = [...issues, ...semanticIssues];

  return (
    <div style={{ padding: "12px", overflowY: "auto", height: "100%" }}>
      <h3 style={{ marginBottom: "8px" }}>Validation</h3>

      {loading && <p style={{ color: "#666" }}>Running validation…</p>}

      {!loading && isValid === true && (
        <p style={{ color: "#388e3c", fontWeight: "bold" }}>No issues found.</p>
      )}

      {!loading && all.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0 }}>
          {all.map((issue, i) => (
            <li
              key={i}
              style={{
                marginBottom: "8px",
                padding: "8px",
                borderLeft: `4px solid ${SEVERITY_COLOR[issue.severity] ?? "#999"}`,
                background: "#fafafa",
              }}
            >
              <strong style={{ color: SEVERITY_COLOR[issue.severity] }}>
                [{issue.severity.toUpperCase()}]
              </strong>{" "}
              <span style={{ fontSize: "0.85em", color: "#555" }}>{issue.rule_id}</span>
              <p style={{ margin: "4px 0 0" }}>{issue.message}</p>
              {issue.suggestion && (
                <p style={{ margin: "2px 0 0", fontSize: "0.85em", color: "#555" }}>
                  Suggestion: {issue.suggestion}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {!loading && isValid === undefined && (
        <p style={{ color: "#999" }}>Upload or edit a diagram to validate.</p>
      )}
    </div>
  );
}
