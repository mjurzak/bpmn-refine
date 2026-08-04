import { describe, expect, it } from "vitest";
import {
  describeUnmetDependency,
  operationDependencies,
  unmetDependencies,
  withDependencies,
} from "./editOpDeps.js";

// a plan where op 1 connects the node op 0 creates
const PLAN = [
  { op: "add_node", id: "task_new", node_type: "task", process_id: "Process_1" },
  {
    op: "add_flow",
    process_id: "Process_1",
    id: "sf_new",
    source_ref: "task_1",
    target_ref: "task_new",
  },
  { op: "set_condition", flow_id: "sf_new", condition_expression: "${x}" },
  { op: "rename_node", id: "task_1", new_name: "Review" },
];

describe("operationDependencies", () => {
  it("links an operation to the one that creates what it references", () => {
    const deps = operationDependencies(PLAN);
    expect(deps.get(1)).toEqual([0]);
    expect(deps.get(2)).toEqual([1]);
  });

  it("does not link operations addressing pre-existing elements", () => {
    // task_1 is in the base diagram, so the rename depends on nothing
    expect(operationDependencies(PLAN).get(3)).toEqual([]);
  });

  it("gives the first operation no dependencies", () => {
    expect(operationDependencies(PLAN).get(0)).toEqual([]);
  });

  it("handles an empty plan", () => {
    expect(operationDependencies([]).size).toBe(0);
  });
});

describe("unmetDependencies", () => {
  it("reports nothing when the whole plan is selected", () => {
    expect(unmetDependencies(PLAN, [0, 1, 2, 3])).toEqual([]);
  });

  it("reports nothing for an independent subset", () => {
    expect(unmetDependencies(PLAN, [3])).toEqual([]);
  });

  it("flags a flow selected without the node it connects to", () => {
    expect(unmetDependencies(PLAN, [1])).toEqual([{ index: 1, missing: [0] }]);
  });

  it("flags each broken operation separately", () => {
    expect(unmetDependencies(PLAN, [1, 2])).toEqual([
      { index: 1, missing: [0] },
    ]);
  });

  it("reports nothing for an empty selection", () => {
    expect(unmetDependencies(PLAN, [])).toEqual([]);
  });
});

describe("withDependencies", () => {
  it("pulls in what a selection requires", () => {
    expect(withDependencies(PLAN, [1])).toEqual([0, 1]);
  });

  it("follows a dependency chain transitively", () => {
    // 2 needs 1, which needs 0
    expect(withDependencies(PLAN, [2])).toEqual([0, 1, 2]);
  });

  it("leaves an already-complete selection unchanged", () => {
    expect(withDependencies(PLAN, [3])).toEqual([3]);
  });
});

describe("describeUnmetDependency", () => {
  it("names the operations in the numbering the panel shows", () => {
    expect(describeUnmetDependency({ index: 1, missing: [0] })).toBe(
      "Operation #2 needs #1 to be selected as well.",
    );
  });
});
