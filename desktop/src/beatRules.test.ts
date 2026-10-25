import { describe, expect, it } from "vitest";
import { BEAT_ACTIONS, KINDS } from "./beatKinds";
import { beatLabel } from "./beatRules";
import type { Beat, BeatAction, SceneObject } from "./types";

const OBJECTS: SceneObject[] = [
  { id: "axes_1", name: "Axes", type: "axes", params: {} },
  { id: "plot_1", name: "Sine", type: "function_plot", params: {} },
  { id: "dot_1", name: "Dot", type: "dot", params: {} },
  { id: "circle_1", name: "Circle", type: "circle", params: {} },
  { id: "rectangle_1", name: "Rectangle", type: "rectangle", params: {} },
];

function beat(action: BeatAction, targets: string[]): Beat {
  return { id: "beat_1", duration: 1, action, targets };
}

describe("beatLabel (design §10.5)", () => {
  it.each([
    ["fade_out", ["axes_1", "plot_1"], "Fade out · Axes, Sine"],
    ["transform", ["circle_1", "rectangle_1"], "Transform · Circle → Rectangle"],
    ["move_along", ["dot_1", "plot_1"], "Move along a path · Dot along Sine"],
    ["wait", [], "Wait"],
    ["move_camera", [], "Move camera"],
    ["set_color", ["plot_1"], "Recolor · Sine"],
  ] as const)("labels %s on %j as %s", (action, targets, label) => {
    expect(beatLabel(beat(action, [...targets]), OBJECTS)).toBe(label);
  });

  it("names a target that is not an object by its id", () => {
    expect(beatLabel(beat("indicate", ["plot_1", "ghost_1"]), OBJECTS)).toBe("Indicate · Sine, ghost_1");
  });

  it("labels a beat of every kind with the kind's label and its targets' names", () => {
    for (const action of BEAT_ACTIONS) {
      const kind = KINDS[action];
      const targets = { none: [], some: ["dot_1"], pair: ["dot_1", "circle_1"] }[kind.arity];
      const label = beatLabel(beat(action, targets), OBJECTS);
      expect(label.startsWith(kind.label)).toBe(true);
      expect(label).toContain(kind.arity === "none" ? kind.label : "Dot");
      expect(label.includes("Circle")).toBe(kind.arity === "pair");
    }
  });
});
