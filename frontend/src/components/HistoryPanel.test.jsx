import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import HistoryPanel from "./HistoryPanel.jsx";
import { getHistory, revertToRevision } from "../api/client.js";

vi.mock("../api/client.js", () => ({
  getHistory: vi.fn(), getRevision: vi.fn(), revertToRevision: vi.fn(), exportDiagram: vi.fn(),
}));

it("reports a failed document update when restoring history", async () => {
  getHistory.mockResolvedValue({ revisions: [
    { rev_id: "0001", index: 1, message: "Old version", author: "user", timestamp: "2026-09-11T12:00:00Z" },
  ] });
  revertToRevision.mockResolvedValue({ diagram: { processes: [] }, new_rev_id: "0003" });
  const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    render(<HistoryPanel sessionId="session" currentRevId="0002" previewRevId="0001"
      onRevert={async () => { throw new Error("Export unavailable"); }} />);
    fireEvent.click(await screen.findByText("Restore this version"));
    await waitFor(() => expect(alert).toHaveBeenCalledWith("Revert failed: Export unavailable"));
  } finally {
    alert.mockRestore();
    consoleError.mockRestore();
  }
});
