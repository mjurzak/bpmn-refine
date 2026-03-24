import React from "react";

const SEVERITY_CLASS = {
  error: "error",
  warning: "warning",
  info: "info",
};

export default function ValidationPanel({
  issues = [],
  semanticIssues = [],
  isValid,
  loading,
  errorCount = 0,
}) {
  const all = [...issues, ...semanticIssues];

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
          <div className="validation-empty">Running validation...</div>
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
          all.map((issue, i) => {
            const sev = SEVERITY_CLASS[issue.severity] ?? "info";
            return (
              <div key={i} className={`issue-card issue-card--${sev}`}>
                <span className={`issue-severity issue-severity--${sev}`}>
                  {issue.severity}
                </span>
                <span className="issue-rule">{issue.rule_id}</span>
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
