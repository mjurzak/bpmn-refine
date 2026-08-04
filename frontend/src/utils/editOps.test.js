import { describe, expect, it } from "vitest";
import { opElementIds, opLabel } from "./editOps.js";

// The op names below are the ones the backend actually emits (EditOpType in
// backend/app/repair/ops.py). The regression these guard is a switch that
// handled `rename_element`, which is not one of them.

describe("operation element ids", () => {
  it("resolves the node a rename addresses", () => {
    expect(opElementIds({ op: "rename_node", id: "task_1" })).toEqual(["task_1"]);
  });

  it("resolves the flow a rename addresses", () => {
    expect(opElementIds({ op: "rename_flow", id: "sf_1" })).toEqual(["sf_1"]);
  });

  it("includes the new flow alongside both endpoints", () => {
    expect(
      opElementIds({
        op: "add_flow",
        id: "sf_new",
        process_id: "proc_1",
        source_ref: "start_1",
        target_ref: "task_1",
      }),
    ).toEqual(["sf_new", "start_1", "task_1"]);
  });

  it("resolves the flow a condition is set on", () => {
    expect(opElementIds({ op: "set_condition", flow_id: "sf_1" })).toEqual(["sf_1"]);
  });

  it("highlights nothing for a whole-diagram replacement", () => {
    expect(opElementIds({ op: "replace_diagram" })).toEqual([]);
  });

  it("survives an unknown or absent operation", () => {
    expect(opElementIds({ op: "invented_op", id: "x" })).toEqual([]);
    expect(opElementIds(null)).toEqual([]);
  });
});

describe("operation labels", () => {
  it("distinguishes a node rename from a flow rename", () => {
    expect(opLabel({ op: "rename_node", id: "task_1", new_name: "Review" })).toBe(
      'Rename node task_1 to "Review"',
    );
    expect(opLabel({ op: "rename_flow", id: "sf_1", new_name: "approved" })).toBe(
      'Rename flow sf_1 to "approved"',
    );
  });

  it("falls back to the raw op name it does not know", () => {
    expect(opLabel({ op: "invented_op" })).toBe("invented_op");
    expect(opLabel(null)).toBe("unknown operation");
  });
});
