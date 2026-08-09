import { describe, expect, it } from "vitest";

import { compareBpmnXml } from "./bpmnXmlDiff.js";

const wrap = (body) => `
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="Defs_1">
  <process id="Process_1">${body}</process>
</definitions>`;

describe("compareBpmnXml", () => {
  it("reports removed and added BPMN elements", () => {
    const original = wrap(`
      <startEvent id="Start_1" />
      <task id="Task_1" name="Review" />
      <sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />
    `);
    const variant = wrap(`
      <task id="Task_1" name="Review" />
      <endEvent id="End_1" />
    `);

    expect(compareBpmnXml(original, variant)).toEqual({
      removed: ["Flow_1", "Start_1"],
      added: ["End_1"],
      modified: [],
    });
  });

  it("reports changed names, types, endpoints, and conditions", () => {
    const original = wrap(`
      <task id="Task_1" name="Review" />
      <sequenceFlow id="Flow_1" sourceRef="Task_1" targetRef="End_1" />
    `);
    const variant = wrap(`
      <userTask id="Task_1" name="Approve" />
      <sequenceFlow id="Flow_1" sourceRef="Task_1" targetRef="End_2">
        <conditionExpression>approved</conditionExpression>
      </sequenceFlow>
    `);

    expect(compareBpmnXml(original, variant).modified).toEqual([
      "Flow_1",
      "Task_1",
    ]);
  });

  it("ignores derived incoming and outgoing children", () => {
    const original = wrap(`
      <task id="Task_1"><incoming>Flow_1</incoming></task>
    `);
    const variant = wrap(`<task id="Task_1" />`);

    expect(compareBpmnXml(original, variant).modified).toEqual([]);
  });
});
