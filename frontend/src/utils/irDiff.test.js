import { describe, expect, it } from "vitest";
import { diffDiagrams, summarizeDiagramDiff } from "./irDiff.js";

function diagram(overrides = {}) {
  return {
    definitions_id: "defs_1",
    processes: [
      {
        id: "Process_1",
        name: "Claim",
        is_executable: true,
        flow_nodes: [
          { id: "start_1", type: "startEvent", name: null },
          { id: "task_1", type: "task", name: "Review" },
          { id: "end_1", type: "endEvent", name: null },
        ],
        sequence_flows: [
          {
            id: "sf_1",
            source_ref: "start_1",
            target_ref: "task_1",
            name: null,
            condition_expression: null,
          },
          {
            id: "sf_2",
            source_ref: "task_1",
            target_ref: "end_1",
            name: null,
            condition_expression: null,
          },
        ],
      },
    ],
    ...overrides,
  };
}

function mutate(fn) {
  const copy = JSON.parse(JSON.stringify(diagram()));
  fn(copy);
  return copy;
}

describe("changes the old diff already reported", () => {
  it("reports an added node", () => {
    const next = mutate((d) =>
      d.processes[0].flow_nodes.push({ id: "task_2", type: "task", name: "Pay" }),
    );
    expect(diffDiagrams(diagram(), next).added).toEqual(["task_2"]);
    expect(summarizeDiagramDiff(diagram(), next)).toContain("+ task Pay (task_2)");
  });

  it("reports a removed node", () => {
    const next = mutate((d) => {
      d.processes[0].flow_nodes = d.processes[0].flow_nodes.filter(
        (node) => node.id !== "task_1",
      );
    });
    expect(diffDiagrams(diagram(), next).removed).toEqual([
      { id: "task_1", name: "Review", type: "task" },
    ]);
  });

  it("reports a renamed node", () => {
    const next = mutate((d) => {
      d.processes[0].flow_nodes[1].name = "Assess";
    });
    expect(diffDiagrams(diagram(), next).modified).toEqual(["task_1"]);
    expect(summarizeDiagramDiff(diagram(), next)).toContain(
      "name: Review -> Assess",
    );
  });

  it("reports a changed node type", () => {
    const next = mutate((d) => {
      d.processes[0].flow_nodes[1].type = "userTask";
    });
    expect(summarizeDiagramDiff(diagram(), next)).toContain(
      "type: task -> userTask",
    );
  });
});

describe("changes the old diff silently dropped", () => {
  it("reports a rerouted flow", () => {
    const next = mutate((d) => {
      d.processes[0].sequence_flows[1].target_ref = "start_1";
    });
    expect(diffDiagrams(diagram(), next).modified).toEqual(["sf_2"]);
    expect(summarizeDiagramDiff(diagram(), next)).toContain(
      "target: end_1 -> start_1",
    );
  });

  it("reports a changed flow source", () => {
    const next = mutate((d) => {
      d.processes[0].sequence_flows[1].source_ref = "start_1";
    });
    expect(summarizeDiagramDiff(diagram(), next)).toContain(
      "source: task_1 -> start_1",
    );
  });

  it("reports a set branch condition", () => {
    const next = mutate((d) => {
      d.processes[0].sequence_flows[1].condition_expression = "${amount > 100}";
    });
    expect(diffDiagrams(diagram(), next).modified).toEqual(["sf_2"]);
    expect(summarizeDiagramDiff(diagram(), next)).toContain(
      "condition: <empty> -> ${amount > 100}",
    );
  });

  it("reports a cleared branch condition", () => {
    const base = mutate((d) => {
      d.processes[0].sequence_flows[1].condition_expression = "${x}";
    });
    const next = mutate((d) => {
      d.processes[0].sequence_flows[1].condition_expression = null;
    });
    expect(summarizeDiagramDiff(base, next)).toContain(
      "condition: ${x} -> <empty>",
    );
  });

  it("reports a renamed flow", () => {
    const next = mutate((d) => {
      d.processes[0].sequence_flows[1].name = "Approved";
    });
    expect(summarizeDiagramDiff(diagram(), next)).toContain(
      "name: <empty> -> Approved",
    );
  });

  it("reports a changed process field", () => {
    const next = mutate((d) => {
      d.processes[0].name = "Reimbursement";
      d.processes[0].is_executable = false;
    });
    const summary = summarizeDiagramDiff(diagram(), next);
    expect(summary).toContain("~ process Process_1");
    expect(summary).toContain("name: Claim -> Reimbursement");
    expect(summary).toContain("executable: true -> false");
  });

  it("shows the endpoints of an added flow", () => {
    const next = mutate((d) =>
      d.processes[0].sequence_flows.push({
        id: "sf_3",
        source_ref: "start_1",
        target_ref: "end_1",
        name: null,
        condition_expression: "${skip}",
      }),
    );
    const summary = summarizeDiagramDiff(diagram(), next);
    expect(summary).toContain("+ sequenceFlow sf_3");
    expect(summary).toContain("start_1 -> end_1");
    expect(summary).toContain("condition: ${skip}");
  });
});

describe("what the diff must not report", () => {
  it("ignores geometry, which no edit operation changes", () => {
    const next = mutate((d) => {
      d.processes[0].flow_nodes[1].bounds = { x: 9, y: 9, width: 1, height: 1 };
      d.processes[0].sequence_flows[0].waypoints = [{ x: 1, y: 2 }];
    });
    expect(diffDiagrams(diagram(), next).modified).toEqual([]);
    expect(summarizeDiagramDiff(diagram(), next)).toBe(
      "  no diagram changes detected",
    );
  });

  it("reports nothing for an identical diagram", () => {
    expect(summarizeDiagramDiff(diagram(), diagram())).toBe(
      "  no diagram changes detected",
    );
  });

  it("handles a missing diagram", () => {
    expect(summarizeDiagramDiff(null, diagram())).toBe(
      "  no diagram changes detected",
    );
    expect(diffDiagrams(null, diagram())).toEqual({
      added: [],
      modified: [],
      removed: [],
    });
  });
});
