import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import * as api from "./api/client.js";
import App from "./App.jsx";

vi.mock("./api/client.js", () => ({
  uploadDiagram: vi.fn(), exportDiagram: vi.fn(), validateDiagram: vi.fn(),
  parseDiagramXml: vi.fn(), repairDiagram: vi.fn(), repairXml: vi.fn(),
  applyEditOps: vi.fn(), commitRevision: vi.fn(),
}));
vi.mock("bpmn-auto-layout", () => ({ layoutProcess: vi.fn(async xml => xml) }));
vi.mock("./components/BpmnEditor.jsx", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return { default: forwardRef(({ xml, onXmlChange }, ref) => {
    useImperativeHandle(ref, () => ({ getXml: async () => xml, resize() {} }));
    return <><output data-testid="xml">{xml}</output>
      <button onClick={() => onXmlChange("edited XML")}>Edit document</button></>;
  }) };
});
vi.mock("./components/ChatPanel.jsx", () => ({ default: ({ ir, onIrUpdate }) =>
  <><output data-testid="ir">{ir?.definitions_id}</output>
    <button onClick={() => onIrUpdate({ definitions_id: "replacement", processes: [] })
      .catch(error => window.alert(error.message))}>Apply replacement</button></>
}));
vi.mock("./components/HistoryPanel.jsx", () => ({ default: () => null }));
vi.mock("./components/LogsPanel.jsx", () => ({ default: () => null }));
vi.mock("./components/CodeView.jsx", () => ({ default: () => null }));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function upload(name) {
  const file = new File([name], `${name}.bpmn`);
  file.text = async () => name;
  fireEvent.change(document.querySelector('input[type="file"]'), { target: { files: [file] } });
  await waitFor(() => expect(screen.getByTestId("xml")).toHaveTextContent(`${name} XML`));
}
const finding = { rule_id: "R001", severity: "error", message: "Old diagram finding" };
const result = { is_valid: false, issues: [finding], semantic_issues: [] };

beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  vi.spyOn(window, "alert").mockImplementation(() => {});
  api.uploadDiagram.mockImplementation(async file => ({
    diagram: { definitions_id: file.name.replace(".bpmn", ""), processes: [] }, session_id: "session",
  }));
  api.exportDiagram.mockImplementation(async ir => ({ xml: `${ir.definitions_id} XML BPMNShape` }));
});

it("discards validation for an imported-away document without ending the newer request", async () => {
  const old = deferred(), current = deferred();
  api.validateDiagram.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  render(<App />);
  await upload("A");
  fireEvent.click(screen.getByRole("button", { name: "Verify Rules" }));
  await upload("B");
  fireEvent.click(screen.getByRole("button", { name: "Verify Rules" }));
  await act(async () => old.resolve(result));
  expect(screen.queryByText(finding.message)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Verify Rules" })).toBeDisabled();
  await act(async () => current.resolve({ is_valid: true, issues: [], semantic_issues: [] }));
  expect(screen.getByText("No issues found")).toBeInTheDocument();
});

it("clears findings when the canvas changes", async () => {
  api.validateDiagram.mockResolvedValue(result);
  render(<App />);
  await upload("A");
  fireEvent.click(screen.getByRole("button", { name: "Verify Rules" }));
  await screen.findByText(finding.message);
  fireEvent.click(screen.getByText("Edit document"));
  expect(screen.queryByText(finding.message)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Repair" })).toBeDisabled();
});

it("ignores a stale validation failure after editing", async () => {
  const pending = deferred();
  api.validateDiagram.mockReturnValue(pending.promise);
  render(<App />);
  await upload("A");
  fireEvent.click(screen.getByRole("button", { name: "Verify Rules" }));
  fireEvent.click(screen.getByText("Edit document"));
  await act(async () => pending.reject(new Error("old request failed")));
  expect(window.alert).not.toHaveBeenCalled();
});

it("preserves the document and propagates an export failure during an update", async () => {
  render(<App />);
  await upload("A");
  api.exportDiagram.mockRejectedValueOnce(new Error("Export unavailable"));
  fireEvent.click(screen.getByText("Apply replacement"));
  await waitFor(() => expect(window.alert).toHaveBeenCalledWith("Export unavailable"));
  expect(screen.getByTestId("xml")).toHaveTextContent("A XML");
  expect(screen.getByTestId("ir")).toHaveTextContent("A");
});

it("does not replace the model when parsing an older edit finishes after an import", async () => {
  const parsed = deferred();
  api.parseDiagramXml.mockReturnValue(parsed.promise);
  render(<App />);
  await upload("A");
  fireEvent.click(screen.getByText("Edit document"));
  fireEvent.click(screen.getByRole("button", { name: "Verify Rules" }));
  await waitFor(() => expect(api.parseDiagramXml).toHaveBeenCalled());
  await upload("B");
  await act(async () => parsed.resolve({ diagram: { definitions_id: "edited A", processes: [] } }));
  expect(screen.getByTestId("ir")).toHaveTextContent("B");
  expect(api.validateDiagram).not.toHaveBeenCalled();
});
