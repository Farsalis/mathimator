import { describe, expect, it } from "vitest";
import canonicalOrder from "../../tests/fixtures/beats/canonical-order.json";
import { BEAT_ACTIONS, KINDS, OBJECT_TYPES } from "./beatKinds";
import {
  beatLabel,
  candidates,
  canonicalTargets,
  checkTarget,
  insertionIndex,
  kindGroups,
  leavesWithAxes,
  plusBeat,
  pointAxesId,
  roleCandidates,
  targetRows,
  targetsForKind,
} from "./beatRules";
import { asString, fieldsOf, listOf, oneOf, optional } from "./parse";
import type { Beat, BeatAction, SceneObject } from "./types";

const OBJECTS: SceneObject[] = [
  { id: "axes_1", name: "Axes", type: "axes", params: {} },
  { id: "plot_1", name: "Sine", type: "function_plot", params: { axesId: "axes_1" } },
  { id: "dot_1", name: "Dot", type: "dot", params: {} },
  { id: "circle_1", name: "Circle", type: "circle", params: {} },
  { id: "rectangle_1", name: "Rectangle", type: "rectangle", params: {} },
];

/** A 2D scene: axes, a plot and a point drawn on them, a text, a formula, a dot and a circle. */
const SCENE: SceneObject[] = [
  { id: "axes_1", name: "Axes", type: "axes", params: {} },
  { id: "plot_1", name: "Sine", type: "function_plot", params: { axesId: "axes_1" } },
  { id: "point_1", name: "Point", type: "axes_point", params: { axesId: "axes_1" } },
  { id: "text_1", name: "Caption", type: "text", params: {} },
  { id: "eq_1", name: "Formula", type: "math_formula", params: {} },
  { id: "dot_1", name: "Dot", type: "dot", params: {} },
  { id: "circle_1", name: "Circle", type: "circle", params: {} },
];

function beat(action: BeatAction, targets: string[]): Beat {
  return { id: "beat_1", duration: 1, action, targets };
}

function ids(objects: readonly SceneObject[]): string[] {
  return objects.map((object) => object.id);
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

/** A case of canonical-order.json, whose objects name their axes at the top level. */
interface OrderCase {
  name: string;
  objects: SceneObject[];
  action: BeatAction;
  targets: string[];
  expected: string[];
}

function asOrderCase(value: unknown, path: string): OrderCase {
  const read = fieldsOf(value, path);
  const objects = read(
    "objects",
    listOf((item, at) => {
      const field = fieldsOf(item, at);
      const id = field("id", asString);
      const type = field("type", oneOf(OBJECT_TYPES));
      const axesId = field("axesId", optional(asString));
      return { id, name: id, type, params: axesId === undefined ? {} : { axesId } };
    }),
  );
  return {
    name: read("name", asString),
    objects,
    action: read("action", oneOf(BEAT_ACTIONS)),
    targets: read("targets", listOf(asString)),
    expected: read("expected", listOf(asString)),
  };
}

describe("canonicalTargets (design §6.1, canonical-order.json)", () => {
  const cases = listOf(asOrderCase)(canonicalOrder.cases, "cases");

  it.each(cases.map((item) => [item.name, item] as const))("orders %s as the worker does", (_, item) => {
    expect(canonicalTargets(item.objects, item.action, item.targets)).toEqual(item.expected);
  });

  it("keeps ids that name no object at the end, in their order", () => {
    expect(canonicalTargets(SCENE, "fade_out", ["ghost_2", "text_1", "ghost_1", "axes_1"])).toEqual([
      "axes_1",
      "text_1",
      "ghost_2",
      "ghost_1",
    ]);
  });
});

describe("kindGroups", () => {
  it("offers every kind of a 2D scene but the camera move, by family in catalog order", () => {
    const groups = kindGroups("scene");
    expect(groups.map((group) => group.family.label)).toEqual([
      "Wait",
      "Entrances",
      "Exits",
      "Changes",
      "Emphasis",
      "Morphs",
    ]);
    expect(groups.flatMap((group) => group.kinds.map((kind) => kind.action))).toEqual(
      BEAT_ACTIONS.filter((action) => action !== "move_camera"),
    );
  });

  it("offers a 3D scene the kinds whose scenes include 3D, and the camera move", () => {
    const offered = kindGroups("three_d_scene").flatMap((group) => group.kinds.map((kind) => kind.action));
    expect(offered).toEqual(BEAT_ACTIONS.filter((action) => KINDS[action].scenes.includes("three_d_scene")));
    expect(offered).toContain("move_camera");
    expect(offered).not.toContain("write");
    expect(offered).not.toContain("transform");
  });
});

describe("candidates (design §10.4)", () => {
  const on = ["axes_1", "plot_1", "text_1"];

  it("offers an entrance the objects of its types that are off screen", () => {
    expect(ids(candidates("fade_in", SCENE, on))).toEqual(["point_1", "eq_1", "dot_1", "circle_1"]);
    expect(ids(candidates("write", SCENE, on))).toEqual(["eq_1"]);
    expect(ids(candidates("grow_arrow", SCENE, on))).toEqual([]);
  });

  it.each(["fade_out", "scale", "indicate"] as const)("offers %s the objects of its types on screen", (action) => {
    expect(ids(candidates(action, SCENE, on))).toEqual(on);
  });

  it("offers each role the objects of its types in the state it needs", () => {
    const [source, destination] = KINDS.transform.roles;
    const [mover, path] = KINDS.move_along.roles;
    if (!source || !destination || !mover || !path) {
      throw new Error("A pair kind has two roles.");
    }
    expect(ids(roleCandidates(source, SCENE, on))).toEqual(on);
    expect(ids(roleCandidates(destination, SCENE, on))).toEqual(["point_1", "eq_1", "dot_1", "circle_1"]);
    expect(ids(roleCandidates(mover, SCENE, ["plot_1", "dot_1", "circle_1"]))).toEqual(["dot_1"]);
    expect(ids(roleCandidates(path, SCENE, ["plot_1", "dot_1", "circle_1"]))).toEqual(["plot_1", "circle_1"]);
  });
});

describe("checkTarget and the children of axes (design §10.4)", () => {
  const on = ["axes_1", "plot_1", "point_1", "text_1"];

  it.each(["fade_out", "move", "scale", "indicate", "wiggle"] as const)(
    "checks the on-screen children of axes with %s, which takes them along",
    (action) => {
      expect(checkTarget(action, SCENE, on, ["text_1"], "axes_1")).toEqual(["axes_1", "plot_1", "point_1", "text_1"]);
    },
  );

  it("checks only the axes with a kind that leaves their children as they are", () => {
    expect(checkTarget("set_color", SCENE, on, ["text_1"], "axes_1")).toEqual(["axes_1", "text_1"]);
  });

  it("checks no child that is off screen or of a type the kind does not take", () => {
    expect(checkTarget("indicate", SCENE, ["axes_1", "plot_1"], [], "axes_1")).toEqual(["axes_1", "plot_1"]);
    expect(checkTarget("unwrite", SCENE, on, [], "axes_1")).toEqual(["axes_1"]);
  });

  it("locks the children of axes an exit takes off screen, only while they are checked", () => {
    const [, plot, point] = SCENE;
    if (!plot || !point) {
      throw new Error("The scene has a plot and a point.");
    }
    expect(leavesWithAxes("fade_out", plot, ["axes_1", "plot_1"])).toBe(true);
    expect(leavesWithAxes("fade_out", plot, ["plot_1"])).toBe(false);
    expect(leavesWithAxes("move", plot, ["axes_1", "plot_1"])).toBe(false);
    expect(targetRows("fade_out", SCENE, on, ["axes_1", "plot_1"])).toEqual([
      { object: SCENE[0], checked: true, fits: true, locked: false },
      { object: plot, checked: true, fits: true, locked: true },
      { object: point, checked: false, fits: true, locked: false },
      { object: SCENE[3], checked: false, fits: true, locked: false },
    ]);
  });

  it("lists the candidates and the current targets in canonical order, marking a target that does not fit", () => {
    const rows = targetRows("fade_in", SCENE, ["axes_1", "dot_1"], ["dot_1", "circle_1"]);
    expect(rows.map((row) => [row.object.id, row.checked, row.fits])).toEqual([
      ["plot_1", false, true],
      ["point_1", false, true],
      ["text_1", false, true],
      ["eq_1", false, true],
      ["dot_1", true, false],
      ["circle_1", true, true],
    ]);
  });
});

describe("targetsForKind (design §10.4)", () => {
  const on = ["axes_1", "plot_1", "text_1", "dot_1"];

  it("gives a wait and a camera move no targets", () => {
    expect(targetsForKind("wait", ["text_1"], "text_1", SCENE, on)).toEqual([]);
    expect(targetsForKind("move_camera", [], null, SCENE, on)).toEqual([]);
  });

  it("keeps the current targets that fit, in canonical order", () => {
    expect(targetsForKind("scale", ["text_1", "circle_1", "dot_1"], "plot_1", SCENE, on)).toEqual(["text_1", "dot_1"]);
  });

  it("takes the selected object when no current target fits", () => {
    expect(targetsForKind("fade_in", ["text_1"], "eq_1", SCENE, on)).toEqual(["eq_1"]);
  });

  it("takes the first object that fits when neither the targets nor the selected object do", () => {
    expect(targetsForKind("fade_in", ["text_1"], "dot_1", SCENE, on)).toEqual(["point_1"]);
    expect(targetsForKind("indicate", [], null, SCENE, on)).toEqual(["axes_1", "plot_1"]);
  });

  it("brings the on-screen children of axes into an exit, which cannot leave them behind", () => {
    expect(targetsForKind("fade_out", ["axes_1"], null, SCENE, on)).toEqual(["axes_1", "plot_1"]);
    expect(targetsForKind("move", ["axes_1"], null, SCENE, on)).toEqual(["axes_1"]);
  });

  it("is null when no object could be a target here, which disables the kind", () => {
    expect(targetsForKind("write", ["text_1"], "text_1", SCENE, ["text_1", "eq_1"])).toBeNull();
    expect(targetsForKind("grow_arrow", [], null, SCENE, on)).toBeNull();
    expect(targetsForKind("fade_out", [], null, SCENE, [])).toBeNull();
  });

  it("keeps a morph's entering destination and starts its source from the selected object", () => {
    expect(targetsForKind("transform", ["text_1", "eq_1"], "dot_1", SCENE, on)).toEqual(["dot_1", "eq_1"]);
    expect(targetsForKind("transform", ["text_1"], "circle_1", SCENE, on)).toEqual(["axes_1", "point_1"]);
    expect(targetsForKind("transform_formula", [], null, SCENE, ["eq_1"])).toBeNull();
  });

  it("starts a move along a path from the selected object in its role", () => {
    const moving = ["plot_1", "dot_1", "circle_1"];
    expect(targetsForKind("move_along", [], "circle_1", SCENE, moving)).toEqual(["dot_1", "circle_1"]);
    expect(targetsForKind("move_along", [], "text_1", SCENE, moving)).toEqual(["dot_1", "plot_1"]);
    expect(targetsForKind("move_along", [], null, SCENE, ["plot_1"])).toBeNull();
  });

  it("finds targets in the state each kind needs, for every kind it does not disable", () => {
    for (const action of BEAT_ACTIONS) {
      const kind = KINDS[action];
      const targets = targetsForKind(action, [], null, SCENE, on);
      if (targets === null) {
        continue;
      }
      const fit = targets.map((id, at) => {
        const role = kind.roles[at];
        const fitting = role === undefined ? candidates(action, SCENE, on) : roleCandidates(role, SCENE, on);
        return ids(fitting).includes(id);
      });
      expect(fit).toEqual(targets.map(() => true));
      expect(targets.length).toBe({ none: 0, some: Math.max(targets.length, 1), pair: 2 }[kind.arity]);
    }
  });
});

describe("+ Beat (design §10.3)", () => {
  const on = ["axes_1", "plot_1", "point_1", "text_1"];

  it("adds a wait with nothing selected, and a camera move in a 3D scene", () => {
    expect(plusBeat("scene", SCENE, on, null)).toEqual({ action: "wait", targets: [] });
    expect(plusBeat("three_d_scene", SCENE, on, null)).toEqual({ action: "move_camera", targets: [] });
  });

  it.each([
    ["eq_1", "write"],
    ["dot_1", "grow"],
    ["circle_1", "create"],
  ] as const)("brings %s on screen with its natural entrance", (objectId, action) => {
    expect(plusBeat("scene", SCENE, on, objectId)).toEqual({ action, targets: [objectId] });
  });

  it("indicates an object on screen, and axes with what is drawn on them", () => {
    expect(plusBeat("scene", SCENE, on, "text_1")).toEqual({ action: "indicate", targets: ["text_1"] });
    expect(plusBeat("scene", SCENE, on, "plot_1")).toEqual({ action: "indicate", targets: ["plot_1"] });
    expect(plusBeat("scene", SCENE, on, "axes_1")).toEqual({
      action: "indicate",
      targets: ["axes_1", "plot_1", "point_1"],
    });
  });

  it("treats a selected object the document no longer holds as no selection", () => {
    expect(plusBeat("scene", SCENE, on, "ghost_1")).toEqual({ action: "wait", targets: [] });
  });

  it("inserts after the selected beat, or at the end", () => {
    const beats = [beat("wait", []), { ...beat("wait", []), id: "beat_2" }];
    expect(insertionIndex(beats, "beat_1")).toBe(1);
    expect(insertionIndex(beats, "beat_2")).toBe(2);
    expect(insertionIndex(beats, null)).toBe(2);
    expect(insertionIndex(beats, "beat_9")).toBe(2);
  });
});

describe("Add's point on axes (design §10.6)", () => {
  /** Two axes, each with a plot; the second also has a point, and a 3D surface names its own axes. */
  const GRAPHS: SceneObject[] = [
    { id: "text_1", name: "Caption", type: "text", params: {} },
    { id: "axes_1", name: "Axes", type: "axes", params: {} },
    { id: "plot_1", name: "Sine", type: "function_plot", params: { axesId: "axes_1" } },
    { id: "axes_2", name: "Axes 2", type: "axes", params: {} },
    { id: "plot_2", name: "Cosine", type: "function_plot", params: { axesId: "axes_2" } },
    { id: "point_1", name: "Point", type: "axes_point", params: { axesId: "axes_2" } },
  ];

  it.each([
    ["the selected axes", "axes_1", ["axes_2"], "axes_1"],
    ["the axes of the selected plot", "plot_2", ["axes_1"], "axes_2"],
    ["the axes of the selected point", "point_1", ["axes_1"], "axes_2"],
    ["the first axes on screen with another object selected", "text_1", ["text_1", "axes_2", "plot_2"], "axes_2"],
    ["the first axes on screen with nothing selected", null, ["axes_2"], "axes_2"],
    ["the first axes in the document with none on screen", null, ["text_1"], "axes_1"],
  ] as const)("puts the point on %s", (_, selectedId, on, axesId) => {
    expect(pointAxesId(GRAPHS, on, selectedId)).toBe(axesId);
  });

  it("puts no point on 3D axes, and none at all without 2D axes", () => {
    const space: SceneObject[] = [
      { id: "axes3d_1", name: "3D axes", type: "three_d_axes", params: {} },
      { id: "surface_1", name: "Surface", type: "surface", params: { axesId: "axes3d_1" } },
    ];
    expect(pointAxesId(space, ["axes3d_1", "surface_1"], "surface_1")).toBeNull();
    expect(pointAxesId([...space, ...GRAPHS], ["axes3d_1"], "axes3d_1")).toBe("axes_1");
  });
});
