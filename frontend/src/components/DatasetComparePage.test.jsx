import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDatasetComparison,
  getDatasetIndex,
} from "../api/client.js";
import DatasetComparePage from "./DatasetComparePage.jsx";

vi.mock("../api/client.js", () => ({
  getDatasetIndex: vi.fn(),
  getDatasetComparison: vi.fn(),
}));

vi.mock("./BpmnComparisonViewer.jsx", () => ({
  default: ({ label, xml }) => <div aria-label={label}>{xml}</div>,
}));

const xml = (body) => `
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="Defs_1">
  <process id="Process_1">${body}</process>
</definitions>`;

const INDEX = {
  version: "v1.3.0",
  seeds: [
    {
      id: "01",
      variants: [
        {
          id: "01_S01_Start_1",
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
          id: "02_S02_End_1",
          operators: ["S02"],
          defect_class: "STRUCT",
          expected_finding: "R002",
          injection_site: ["End_1"],
        },
      ],
    },
  ],
};

function comparison(variantId) {
  const seed = variantId.slice(0, 2);
  const removesStart = variantId.includes("S01");
  return {
    version: "v1.3.0",
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
  });

  it("loads the first seed and only its matched variants", async () => {
    render(<DatasetComparePage />);

    expect(await screen.findByLabelText("Original · Seed 01")).toBeInTheDocument();
    expect(screen.getByLabelText("Matched variant")).toHaveValue(
      "01_S01_Start_1",
    );
    expect(
      within(screen.getByLabelText("Visual diff legend")).getByText("Start_1"),
    ).toBeInTheDocument();
    expect(getDatasetComparison).toHaveBeenCalledWith(
      "v1.3.0",
      "01_S01_Start_1",
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
        "02_S02_End_1",
      );
    });
    expect(await screen.findByLabelText("Variant · 02_S02_End_1")).toBeInTheDocument();
  });
});
