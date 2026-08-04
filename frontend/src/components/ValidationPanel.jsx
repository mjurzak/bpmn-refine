import React from "react";
import { sortValidationFindings } from "../utils/validationFindings.js";

const SEVERITY_CLASS = {
  error: "error",
  warning: "warning",
  info: "info",
};

function originLabel(issue, origin, modelUsed) {
  if (issue.tier === "tier3" || origin === "llm") {
    return `tier 3 · ${modelUsed ?? "LLM"}`;
  }
  if (issue.tier === "tier2") {
    return `tier 2 · ${issue.source ?? "formal checker"}`;
  }
  if (issue.tier === "tier1") return "tier 1 · rule engine";
  if (!issue.source || issue.source === "rules") return "rule engine";
  return issue.source;
}

export default function ValidationPanel({
  issues = [],
  semanticIssues = [],
  isValid,
  loading,
  loadingLabel = "Running validation...",
  errorCount = 0,
  modelUsed = null,
}) {
  // issue.source is the only origin signal that survives a repair — after one,
  // remaining_issues is a flat list and the array an issue came in says nothing
  const all = sortValidationFindings([...issues, ...semanticIssues]).map(
    (issue) => ({
      issue,
      origin: issue.source === "llm" ? "llm" : "rules",
    }),
  );

  // pick a badge variant for the header
  let badge = null;
  if (loading) {
    badge = <span className="spinner" />;
  } else if (all.length > 0) {
    badge = (
      <span className={`badge${errorCount > 0 ? " badge--error" : ""}`}>
        {all.length}
      </span>
    );
  } else if (isValid === true) {
    badge = <span className="badge badge--success">0</span>;
  }

  return (
    <>
      <div className="panel-header">
        Validation {badge}
      </div>

      <div className="validation-body">
        {loading && (
          <div className="validation-empty">{loadingLabel}</div>
        )}

        {!loading && isValid === true && (
          <div className="validation-valid">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
            No issues found
          </div>
        )}

        {!loading &&
          all.map(({ issue, origin }, i) => {
            const sev = SEVERITY_CLASS[issue.severity] ?? "info";
            return (
              <div key={i} className={`issue-card issue-card--${sev}`}>
                <span className={`issue-severity issue-severity--${sev}`}>
                  {issue.severity}
                </span>
                <span className="issue-rule">{issue.rule_id}</span>
                <span className={`issue-origin issue-origin--${origin}`}>
                  {originLabel(issue, origin, modelUsed)}
                </span>
                <p className="issue-message">{issue.message}</p>
                {issue.suggestion && (
                  <p className="issue-suggestion">{issue.suggestion}</p>
                )}
              </div>
            );
          })}

        {!loading && isValid === undefined && (
          <div className="validation-empty">
            Upload a diagram and validate to see results here.
          </div>
        )}
      </div>
    </>
  );
}
