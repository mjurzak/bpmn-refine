import { describe, expect, it, vi } from "vitest";
import {
  CONDITION_EXPRESSION_MAX_LABEL_LENGTH,
  createConditionExpressionLabel,
  createConditionExpressionOverlayManager,
  formatConditionExpression,
  getConditionExpressionBody,
  getConditionExpressionLabelPosition,
} from "./conditionExpressionOverlays.js";

describe("condition expression overlays", () => {
  it("reads the body from the imported business object", () => {
    const element = {
      businessObject: {
        $type: "bpmn:SequenceFlow",
        conditionExpression: { body: "${false}" },
      },
    };

    expect(getConditionExpressionBody(element)).toBe("${false}");
    expect(getConditionExpressionBody({ businessObject: {} })).toBeNull();
  });

  it("truncates labels while retaining the full value in the title", () => {
    const body = "${aVeryLongConditionExpressionThatNeedsA <concise> label}";
    const label = createConditionExpressionLabel(body);

    expect(label.textContent).toBe(
      formatConditionExpression(body, CONDITION_EXPRESSION_MAX_LABEL_LENGTH),
    );
    expect(label.textContent).toContain("…");
    expect(label.title).toBe(body);

    const safeLabel = createConditionExpressionLabel("${<concise>}");
    expect(safeLabel.textContent).toBe("${<concise>}");
    expect(safeLabel.innerHTML).toBe("${&lt;concise&gt;}");
  });

  it("places a straight-flow label at the segment midpoint with an offset", () => {
    expect(
      getConditionExpressionLabelPosition({
        waypoints: [
          { x: 100, y: 40 },
          { x: 200, y: 40 },
        ],
      }),
    ).toEqual({ left: 50, top: 12 });
  });

  it("uses the longest segment for a multi-segment flow", () => {
    const position = getConditionExpressionLabelPosition({
      waypoints: [
        { x: 20, y: 20 },
        { x: 20, y: 60 },
        { x: 160, y: 60 },
        { x: 160, y: 100 },
      ],
    });

    expect(position).toEqual({ left: 70, top: 52 });
  });

  it("clears its own overlays before rendering again", () => {
    const remove = vi.fn();
    const add = vi.fn(() => `overlay-${add.mock.calls.length}`);
    const viewer = {
      get(service) {
        if (service === "overlays") return { add, remove };
        if (service === "elementRegistry") {
          return {
            getAll: () => [
              {
                type: "bpmn:SequenceFlow",
                waypoints: [
                  { x: 0, y: 0 },
                  { x: 100, y: 0 },
                ],
                businessObject: {
                  conditionExpression: { body: "${false}" },
                },
              },
            ],
          };
        }
        return null;
      },
    };
    const manager = createConditionExpressionOverlayManager(viewer);

    manager.render();
    manager.render();

    expect(add).toHaveBeenCalledTimes(2);
    expect(add.mock.calls[0][2]).toMatchObject({ scale: true });
    expect(remove).toHaveBeenCalledWith("overlay-1");
    manager.destroy();
    expect(remove).toHaveBeenCalledWith("overlay-2");
  });

  it("does not render a second overlay for a sequence flow external label", () => {
    const add = vi.fn(() => "overlay-1");
    const conditionExpression = { body: "${amount >= 100}" };
    const businessObject = {
      $type: "bpmn:SequenceFlow",
      conditionExpression,
    };
    const viewer = {
      get(service) {
        if (service === "overlays") return { add, remove: vi.fn() };
        if (service === "elementRegistry") {
          return {
            getAll: () => [
              {
                type: "bpmn:SequenceFlow",
                waypoints: [
                  { x: 0, y: 0 },
                  { x: 100, y: 0 },
                ],
                businessObject,
              },
              {
                type: "label",
                businessObject,
              },
            ],
          };
        }
        return null;
      },
    };

    createConditionExpressionOverlayManager(viewer).render();

    expect(add).toHaveBeenCalledTimes(1);
  });
});
