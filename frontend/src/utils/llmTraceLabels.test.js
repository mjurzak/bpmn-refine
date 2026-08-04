import { describe, expect, it } from "vitest";
import { llmTraceTitle } from "./llmTraceLabels.js";

describe("LLM trace titles", () => {
  it("distinguishes repair and semantic calls in one repair request", () => {
    expect(
      llmTraceTitle("Repair proposal", { task: "repair" }, 0, 2),
    ).toBe("Repair LLM call 1");
    expect(
      llmTraceTitle(
        "Repair proposal",
        { task: "semantic_validation" },
        1,
        2,
      ),
    ).toBe("Semantic validation LLM call 2");
  });

  it("keeps the parent action for older traces without task metadata", () => {
    expect(llmTraceTitle("Repair proposal", {}, 0, 1)).toBe(
      "Repair proposal LLM call",
    );
  });
});
