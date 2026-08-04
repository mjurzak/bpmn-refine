const SEVERITY_PRIORITY = {
  error: 0,
  warning: 1,
  info: 2,
};

export function sortValidationFindings(findings = []) {
  return findings
    .map((finding, index) => ({ finding, index }))
    .sort(
      (left, right) =>
        (SEVERITY_PRIORITY[left.finding.severity] ?? 3) -
          (SEVERITY_PRIORITY[right.finding.severity] ?? 3) ||
        left.index - right.index,
    )
    .map(({ finding }) => finding);
}
