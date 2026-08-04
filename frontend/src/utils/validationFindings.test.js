import { describe, expect, it } from "vitest";
import { sortValidationFindings } from "./validationFindings.js";

describe("validation finding ordering", () => {
  it("shows errors before warnings across validation tiers", () => {
    const tier1Warning = { rule_id: "R009", severity: "warning" };
    const tier3Error = {
      rule_id: "semantic:improper_termination",
      severity: "error",
    };

    expect(sortValidationFindings([tier1Warning, tier3Error])).toEqual([
      tier3Error,
      tier1Warning,
    ]);
  });

  it("preserves the original order within the same severity", () => {
    const findings = [
      { rule_id: "warning:first", severity: "warning" },
      { rule_id: "error:first", severity: "error" },
      { rule_id: "warning:second", severity: "warning" },
      { rule_id: "error:second", severity: "error" },
    ];

    expect(sortValidationFindings(findings).map((issue) => issue.rule_id)).toEqual([
      "error:first",
      "error:second",
      "warning:first",
      "warning:second",
    ]);
  });

  it("does not mutate the source array", () => {
    const findings = [
      { rule_id: "warning", severity: "warning" },
      { rule_id: "error", severity: "error" },
    ];

    sortValidationFindings(findings);

    expect(findings.map((issue) => issue.rule_id)).toEqual(["warning", "error"]);
  });
});
