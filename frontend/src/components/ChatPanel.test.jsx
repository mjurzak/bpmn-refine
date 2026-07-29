// Tests for the manual review gate — the human-in-the-loop guarantee the thesis
// rests on. Until now the frontend had no test suite at all, so acceptance,
// subset acceptance, rejection, and complete-diagram proposals were only
// demonstrated in the case study.

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client.js", () => ({
  sendChatMessage: vi.fn(),
  applyEditOps: vi.fn(),
}));

import { applyEditOps } from "../api/client.js";
import ChatPanel from "./ChatPanel.jsx";

const BASE_DIAGRAM = {
  definitions_id: "defs_1",
  processes: [
    {
      id: "Process_1",
      name: "Claim",
      is_executable: true,
      flow_nodes: [
        { id: "start_1", type: "startEvent", name: null },
        { id: "task_1", type: "task", name: "Review" },
      ],
      sequence_flows: [
        {
          id: "sf_1",
          source_ref: "start_1",
          target_ref: "task_1",
          name: null,
          condition_expression: null,
        },
      ],
    },
  ],
};

// op 1 connects the node op 0 creates, so it cannot be applied alone
const OPS = [
  { op: "add_node", id: "end_1", node_type: "endEvent", process_id: "Process_1" },
  { op: "add_flow", id: "sf_2", source_ref: "task_1", target_ref: "end_1" },
];

function withNode(diagram, node, flow) {
  const copy = JSON.parse(JSON.stringify(diagram));
  copy.processes[0].flow_nodes.push(node);
  if (flow) copy.processes[0].sequence_flows.push(flow);
  return copy;
}

const PROPOSED = withNode(
  BASE_DIAGRAM,
  { id: "end_1", type: "endEvent", name: null },
  { id: "sf_2", source_ref: "task_1", target_ref: "end_1", name: null, condition_expression: null },
);

function repairProposal(overrides = {}) {
  return {
    source: "repair",
    baseDiagram: BASE_DIAGRAM,
    diagram: PROPOSED,
    message: "repair proposal",
    remainingIssues: [],
    ops: OPS,
    diff: "+ endEvent end_1\n+ sequenceFlow sf_2",
    ...overrides,
  };
}

function renderPanel(props = {}) {
  const onApplyProposal = vi.fn();
  const onRejectProposal = vi.fn();
  render(
    <ChatPanel
      ir={BASE_DIAGRAM}
      issues={[]}
      sessionId="session_1"
      pendingProposal={repairProposal()}
      onApplyProposal={onApplyProposal}
      onRejectProposal={onRejectProposal}
      SendIcon={() => <span>send</span>}
      {...props}
    />,
  );
  return { onApplyProposal, onRejectProposal };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("manual acceptance of a repair proposal", () => {
  it("starts with every operation selected", () => {
    renderPanel();
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(2);
    checkboxes.forEach((box) => expect(box).toBeChecked());
  });

  it("shows the proposal diff before anything is applied", () => {
    renderPanel();
    expect(screen.getByText(/\+ endEvent end_1/)).toBeInTheDocument();
  });

  it("applies the full operation list when accepted unchanged", async () => {
    const { onApplyProposal } = renderPanel();
    await userEvent.click(screen.getByRole("button", { name: /Apply selected/ }));
    expect(onApplyProposal).toHaveBeenCalledWith(OPS);
  });

  it("does not apply anything on render", () => {
    const { onApplyProposal } = renderPanel();
    expect(onApplyProposal).not.toHaveBeenCalled();
  });
});

describe("subset acceptance", () => {
  it("applies only the operations left selected", async () => {
    applyEditOps.mockResolvedValue({
      updated_diagram: BASE_DIAGRAM,
      op_results: [{ op: OPS[0], applied: true, error: null }],
    });

    const { onApplyProposal } = renderPanel();
    // deselect the dependent flow, keeping the independent add_node
    await userEvent.click(screen.getAllByRole("checkbox")[1]);
    await userEvent.click(screen.getByRole("button", { name: /Apply selected/ }));

    expect(onApplyProposal).toHaveBeenCalledWith([OPS[0]]);
  });

  it("recomputes the preview for the selected subset", async () => {
    applyEditOps.mockResolvedValue({
      updated_diagram: withNode(BASE_DIAGRAM, {
        id: "end_1",
        type: "endEvent",
        name: null,
      }),
      op_results: [{ op: OPS[0], applied: true, error: null }],
    });

    renderPanel();
    await userEvent.click(screen.getAllByRole("checkbox")[1]);

    await waitFor(() =>
      expect(applyEditOps).toHaveBeenCalledWith(BASE_DIAGRAM, [OPS[0]]),
    );
    // the subset adds the node but not the flow the full proposal included
    await waitFor(() =>
      expect(screen.getByText(/preview of the 1 selected operation/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/sequenceFlow sf_2/)).not.toBeInTheDocument();
  });

  it("reports a dependency the selection leaves unmet", async () => {
    renderPanel();
    // deselect the add_node the flow depends on
    await userEvent.click(screen.getAllByRole("checkbox")[0]);

    expect(
      await screen.findByText("Operation #2 needs #1 to be selected as well."),
    ).toBeInTheDocument();
  });

  it("blocks apply while a dependency is unmet", async () => {
    renderPanel();
    await userEvent.click(screen.getAllByRole("checkbox")[0]);

    expect(screen.getByRole("button", { name: /Apply selected/ })).toBeDisabled();
  });

  it("can pull in the operations a selection requires", async () => {
    renderPanel();
    await userEvent.click(screen.getAllByRole("checkbox")[0]);
    await userEvent.click(
      screen.getByRole("button", { name: "Select required operations" }),
    );

    await waitFor(() =>
      screen.getAllByRole("checkbox").forEach((box) => expect(box).toBeChecked()),
    );
    expect(screen.getByRole("button", { name: /Apply selected/ })).toBeEnabled();
  });

  it("surfaces a subset the backend refuses to apply", async () => {
    applyEditOps.mockResolvedValue({
      updated_diagram: BASE_DIAGRAM,
      op_results: [
        { op: OPS[0], applied: false, error: "element id 'end_1' already exists" },
      ],
    });

    renderPanel();
    await userEvent.click(screen.getAllByRole("checkbox")[1]);

    expect(
      await screen.findByText(/element id 'end_1' already exists/),
    ).toBeInTheDocument();
  });

  it("says so when nothing is selected", async () => {
    renderPanel();
    await userEvent.click(screen.getAllByRole("checkbox")[0]);
    await userEvent.click(screen.getAllByRole("checkbox")[1]);

    expect(await screen.findByText(/no operations selected/)).toBeInTheDocument();
    // the empty subset is answered locally rather than round-tripped
    expect(applyEditOps).not.toHaveBeenCalledWith(BASE_DIAGRAM, []);
  });
});

describe("rejection", () => {
  it("reports the rejection without applying anything", async () => {
    const { onRejectProposal, onApplyProposal } = renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(onRejectProposal).toHaveBeenCalled();
    expect(onApplyProposal).not.toHaveBeenCalled();
    expect(applyEditOps).not.toHaveBeenCalled();
  });
});

describe("complete-diagram proposals", () => {
  const chatProposal = repairProposal({
    source: "chat",
    ops: [],
    diff: "+ endEvent end_1",
  });

  it("is reviewed as one all-or-nothing decision", () => {
    renderPanel({ pendingProposal: chatProposal });

    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
  });

  it("applies with no operation list", async () => {
    const { onApplyProposal } = renderPanel({ pendingProposal: chatProposal });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(onApplyProposal).toHaveBeenCalledWith(null);
    expect(applyEditOps).not.toHaveBeenCalled();
  });

  it("names the source of the proposal", () => {
    renderPanel({ pendingProposal: chatProposal });
    expect(screen.getByText("Chat proposal")).toBeInTheDocument();
  });

  it("labels a repair proposal as such", () => {
    renderPanel();
    expect(screen.getByText("Repair proposal")).toBeInTheDocument();
  });
});
