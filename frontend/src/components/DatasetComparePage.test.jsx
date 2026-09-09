import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDatasetComparison,
  getDatasetIndex,
  getEnhancementComparison,
  getEnhancementIndex,
} from "../api/client.js";
import DatasetComparePage from "./DatasetComparePage.jsx";

vi.mock("../api/client.js", () => ({
  getDatasetIndex: vi.fn(),
  getDatasetComparison: vi.fn(),
  getEnhancementIndex: vi.fn(),
  getEnhancementComparison: vi.fn(),
}));

vi.mock("./BpmnComparisonViewer.jsx", () => ({
  default: ({ label, xml }) => <div aria-label={label}>{xml}</div>,
}));

const xml = (body) => `
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="Defs_1">
  <process id="Process_1">${body}</process>
</definitions>`;

const INDEX = {
  version: "v1.0",
  seeds: [
    {
      id: "01",
      variants: [
        {
          id: "single/S01/01",
          operators: ["S01"],
          defect_class: "STRUCT",
          expected_finding: "R001",
          injection_site: ["Start_1"],
        },
      ],
    },
    {
      id: "02",
      variants: [
        {
          id: "single/S02/02",
          operators: ["S02"],
          defect_class: "STRUCT",
          expected_finding: "R002",
          injection_site: ["End_1"],
        },
      ],
    },
  ],
};

const ENHANCEMENT_INDEX = {
  cases: [
    {
      id: "M01-refine-01",
      seed_id: "01",
      operator: "M01",
      instruction: "Add the approval task.",
      relation_type: "exists_task",
      expected_element_ids: ["Task_2"],
    },
  ],
};

const ENHANCEMENT_COMPARISON = {
  case: ENHANCEMENT_INDEX.cases[0],
  core_xml: xml('<startEvent id="Start_1" /><task id="Task_1" />'),
  reference_xml: xml(
    '<startEvent id="Start_1" /><task id="Task_1" /><task id="Task_2" />',
  ),
  relation: { type: "exists_task" },
  d_core: "The request arrives.",
  d_extra: "The worker approves the request.",
};

function comparison(variantId) {
  const seed = variantId.split("/").at(-1);
  const removesStart = variantId.includes("S01");
  return {
    version: "v1.0",
    seed,
    variant: INDEX.seeds
      .flatMap((item) => item.variants)
      .find((item) => item.id === variantId),
    original_xml: xml(
      '<startEvent id="Start_1" /><task id="Task_1" /><endEvent id="End_1" />',
    ),
    variant_xml: xml(
      removesStart
        ? '<task id="Task_1" /><endEvent id="End_1" />'
        : '<startEvent id="Start_1" /><task id="Task_1" />',
    ),
  };
}

describe("DatasetComparePage", () => {
  beforeEach(() => {
    getDatasetIndex.mockResolvedValue(INDEX);
    getDatasetComparison.mockImplementation((_, variantId) =>
      Promise.resolve(comparison(variantId)),
    );
    getEnhancementIndex.mockResolvedValue(ENHANCEMENT_INDEX);
    getEnhancementComparison.mockResolvedValue(ENHANCEMENT_COMPARISON);
  });

  it("loads the first seed and only its matched variants", async () => {
    render(<DatasetComparePage />);

    expect(await screen.findByLabelText("Original · Seed 01")).toBeInTheDocument();
    expect(screen.getByLabelText("Matched variant")).toHaveValue(
      "single/S01/01",
    );
    expect(
      within(screen.getByLabelText("Visual diff legend")).getByText("Start_1"),
    ).toBeInTheDocument();
    expect(getDatasetComparison).toHaveBeenCalledWith(
      "v1.0",
      "single/S01/01",
      expect.any(AbortSignal),
    );
  });

  it("switches the right side to the first variant matched to a new seed", async () => {
    const user = userEvent.setup();
    render(<DatasetComparePage />);
    await screen.findByLabelText("Original · Seed 01");

    await user.selectOptions(screen.getByLabelText("Original seed"), "02");

    await waitFor(() => {
      expect(screen.getByLabelText("Matched variant")).toHaveValue(
        "single/S02/02",
      );
    });
    expect(
      await screen.findByLabelText("Variant · single/S02/02"),
    ).toBeInTheDocument();
  });

  it("shows the enhancement core, reference, and semantic review contract", async () => {
    const user = userEvent.setup();
    render(<DatasetComparePage />);
    await screen.findByLabelText("Original · Seed 01");

    await user.selectOptions(screen.getByLabelText("Dataset view"), "enhancement");

    expect(await screen.findByLabelText("M_core · Seed 01")).toBeInTheDocument();
    expect(screen.getByLabelText("Enhancement case")).toHaveValue("M01-refine-01");
    expect(screen.getByLabelText("Enhancement contract")).toHaveTextContent(
      "Add the approval task.",
    );
    expect(
      within(screen.getByLabelText("Visual diff legend")).getByText("Task_2"),
    ).toBeInTheDocument();
    expect(getEnhancementComparison).toHaveBeenCalledWith(
      "M01-refine-01",
      expect.any(AbortSignal),
    );
  });
});
