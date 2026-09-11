import React from "react";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import ValidationPanel from "./ValidationPanel.jsx";

it("does not announce no issues when warnings remain", () => {
  render(<ValidationPanel isValid issues={[
    { rule_id: "R008", severity: "warning", message: "Review this flow" },
  ]} />);
  expect(screen.queryByText("No issues found")).not.toBeInTheDocument();
  expect(screen.getByText("No errors found; review the findings below")).toBeInTheDocument();
  expect(screen.getByText("Review this flow")).toBeInTheDocument();
});
