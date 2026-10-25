import { describe, expect, it } from "vitest";
import editOps from "../../tests/fixtures/protocol/edit-ops.json";
import { BEAT_ACTIONS, EASING_NAMES, SHAPE_TYPES } from "./beatKinds";
import * as edits from "./edits";
import { EXPORT_FORMATS } from "./exportProfile";
import {
  asBoolean,
  asInteger,
  asNumber,
  asRecord,
  asString,
  fieldsOf,
  isRecord,
  listOf,
  nullable,
  oneOf,
  optional,
  pairOf,
  type Read,
} from "./parse";
import { ANCHORS, AXES, CAMERA_KEYS, LENGTH_AXES, THREE_D_MODES, type Edit, type ParamValue } from "./types";

/** An op of edit-ops.json: its fields with their types in the fixture's grammar, and edits the worker accepts. */
interface OpSpec {
  fields: Record<string, string>;
  examples: { start: string; edit: Record<string, unknown> }[];
}

function recordOf<T>(read: Read<T>): Read<Record<string, T>> {
  return (value, path) =>
    Object.fromEntries(Object.entries(asRecord(value, path)).map(([key, item]) => [key, read(item, `${path}.${key}`)]));
}

function asOpSpec(value: unknown, path: string): OpSpec {
  const read = fieldsOf(value, path);
  return {
    fields: read("fields", recordOf(asString)),
    examples: read(
      "examples",
      listOf((example, at) => {
        const field = fieldsOf(example, at);
        return { start: field("start", asString), edit: field("edit", asRecord) };
      }),
    ),
  };
}

const ENUMS = recordOf(listOf(asString))(editOps.enums, "enums");
const OPS = recordOf(asOpSpec)(editOps.ops, "ops");

/** A beat or shape setting as an edit carries it: a number, an `[x, y]` vector, a colour or an id. */
function asParamValue(value: unknown, path: string): ParamValue {
  return typeof value === "number" || typeof value === "string" ? value : pairOf(asNumber)(value, path);
}

function asTriple(value: unknown, path: string): [number, number, number] {
  const items = listOf(asNumber)(value, path);
  const [a, b, c] = items;
  if (items.length !== 3 || a === undefined || b === undefined || c === undefined) {
    throw new Error(`${path} is not three numbers`);
  }
  return [a, b, c];
}

function asComponent(value: unknown, path: string): 0 | 1 | 2 {
  const index = asInteger(value, path);
  if (index !== 0 && index !== 1 && index !== 2) {
    throw new Error(`${path} is not a component index`);
  }
  return index;
}

/** Reads one field of an example edit with the reader of its type. */
type Field = <T>(key: string, read: Read<T>) => T;

/** How to call each op's builder from the fields of an example edit. */
const BUILDERS: { [Op in Edit["op"]]: (field: Field) => edits.EditOf<Op> } = {
  set_project_name: (field) => edits.setProjectName(field("name", asString)),
  set_object_name: (field) => edits.setObjectName(field("objectId", asString), field("name", asString)),
  set_object_color: (field) => edits.setObjectColor(field("objectId", asString), field("color", asString)),
  set_expression: (field) => edits.setExpression(field("objectId", asString), field("expression", asString)),
  set_latex: (field) => edits.setLatex(field("objectId", asString), field("latex", asString)),
  set_text: (field) => edits.setText(field("objectId", asString), field("text", asString)),
  set_anchor: (field) => edits.setAnchor(field("objectId", asString), field("anchor", oneOf(ANCHORS))),
  set_object_shift: (field) => edits.setObjectShift(field("objectId", asString), field("shift", pairOf(asNumber))),
  set_axes_range: (field) =>
    edits.setAxesRange(field("objectId", asString), field("axis", oneOf(AXES)), field("value", asTriple)),
  set_length: (field) =>
    edits.setLength(field("objectId", asString), field("axis", oneOf(LENGTH_AXES)), field("value", asNumber)),
  set_show_labels: (field) => edits.setShowLabels(field("objectId", asString), field("value", asBoolean)),
  set_domain: (field) => edits.setDomain(field("objectId", asString), field("xMin", asNumber), field("xMax", asNumber)),
  set_beat_duration: (field) => edits.setBeatDuration(field("beatId", asString), field("duration", asNumber)),
  set_beat_color: (field) => edits.setBeatColor(field("beatId", asString), field("color", asString)),
  set_beat_shift: (field) => edits.setBeatShift(field("beatId", asString), field("shift", pairOf(asNumber))),
  add_axes_function: (field) => edits.addAxesFunction(field("afterBeatId", nullable(asString))),
  add_formula_transform: (field) => edits.addFormulaTransform(field("afterBeatId", nullable(asString))),
  add_three_d_axes: (field) => edits.addThreeDAxes(field("afterBeatId", nullable(asString))),
  add_formula: (field) =>
    edits.addFormula(field("afterBeatId", nullable(asString)), field("latex", optional(asString))),
  add_text: (field) => edits.addText(field("afterBeatId", nullable(asString)), field("text", optional(asString))),
  delete_object: (field) => edits.deleteObject(field("objectId", asString)),
  set_three_d_mode: (field) => edits.setThreeDMode(field("mode", oneOf(THREE_D_MODES))),
  set_camera: (field) => edits.setCamera(field("key", oneOf(CAMERA_KEYS)), field("value", asNumber)),
  set_resolution: (field) => edits.setResolution(field("objectId", asString), field("value", pairOf(asInteger))),
  set_curve_component: (field) =>
    edits.setCurveComponent(field("objectId", asString), field("index", asComponent), field("expression", asString)),
  set_export_profile: (field) =>
    edits.setExportProfile({
      format: field("format", oneOf(EXPORT_FORMATS)),
      width: field("width", asInteger),
      height: field("height", asInteger),
      fps: field("fps", asInteger),
      transparent: field("transparent", asBoolean),
    }),
  add_beat: (field) =>
    edits.addBeat(
      field("afterBeatId", nullable(asString)),
      field("action", oneOf(BEAT_ACTIONS)),
      field("targets", listOf(asString)),
      field("params", optional(recordOf(asParamValue))),
    ),
  set_beat_action: (field) =>
    edits.setBeatAction(
      field("beatId", asString),
      field("action", oneOf(BEAT_ACTIONS)),
      field("targets", optional(listOf(asString))),
    ),
  set_beat_targets: (field) => edits.setBeatTargets(field("beatId", asString), field("targets", listOf(asString))),
  set_beat_param: (field) =>
    edits.setBeatParam(field("beatId", asString), field("key", asString), field("value", asParamValue)),
  set_beat_easing: (field) =>
    edits.setBeatEasing(field("beatId", asString), field("easing", nullable(oneOf(EASING_NAMES)))),
  delete_beat: (field) => edits.deleteBeat(field("beatId", asString)),
  move_beat: (field) => edits.moveBeat(field("beatId", asString), field("toIndex", asInteger)),
  add_shape: (field) =>
    edits.addShape(
      field("kind", oneOf(SHAPE_TYPES)),
      field("afterBeatId", nullable(asString)),
      field("axesId", optional(asString)),
    ),
  set_shape_param: (field) =>
    edits.setShapeParam(field("objectId", asString), field("key", asString), field("value", asParamValue)),
};

/** Whether `value` has the type `grammar` names in edit-ops.json. */
function matches(value: unknown, grammar: string): boolean {
  if (grammar.endsWith("?")) {
    return value === undefined || matches(value, grammar.slice(0, -1));
  }
  if (grammar.endsWith("|null")) {
    return value === null || matches(value, grammar.slice(0, -"|null".length));
  }
  if (grammar.startsWith("enum:")) {
    return (ENUMS[grammar.slice("enum:".length)] ?? []).some((option) => option === value);
  }
  const list = /^(\w+)\[(\d*)\]$/.exec(grammar);
  if (list) {
    const [, item = "", length = ""] = list;
    return (
      Array.isArray(value) &&
      (length === "" || value.length === Number(length)) &&
      value.every((element) => matches(element, item))
    );
  }
  switch (grammar) {
    case "string":
      return typeof value === "string";
    case "id":
      return typeof value === "string" && value !== "";
    case "number":
      return typeof value === "number";
    case "integer":
      return Number.isInteger(value);
    case "boolean":
      return typeof value === "boolean";
    case "color":
      return typeof value === "string" && /^#[0-9A-Fa-f]{6}$/.test(value);
    case "object":
      return isRecord(value);
    case "param":
      return ["number", "number[2]", "color", "id"].some((type) => matches(value, type));
    default:
      throw new Error(`edit-ops.json names an unknown field type ${grammar}`);
  }
}

const BUILDER_OF = new Map<string, (field: Field) => Edit>(Object.entries(BUILDERS));
const EXAMPLES = Object.entries(OPS).flatMap(([op, spec]) =>
  spec.examples.map((example, index) => ({ op, fields: spec.fields, index, ...example })),
);

describe("the edit builders (edit-ops.json)", () => {
  it("cover exactly the ops the worker accepts, each with an example", () => {
    expect([...BUILDER_OF.keys()].sort()).toEqual(Object.keys(OPS).sort());
    for (const spec of Object.values(OPS)) {
      expect(spec.examples.length).toBeGreaterThan(0);
    }
  });

  it("use the worker's names for actions, easings, shapes and the other enums", () => {
    expect(ENUMS).toEqual({
      anchor: ANCHORS,
      axis: AXES,
      three_d_mode: THREE_D_MODES,
      camera_key: CAMERA_KEYS,
      format: EXPORT_FORMATS,
      action: BEAT_ACTIONS,
      easing: EASING_NAMES,
      shape: SHAPE_TYPES,
    });
  });

  it.each(EXAMPLES)("build example $index of $op from its fields, as the worker accepts it", ({ op, fields, edit }) => {
    const { op: name, ...given } = edit;
    expect(name).toBe(op);
    for (const [key, value] of Object.entries(given)) {
      expect(fields[key], `${op}.${key} is not a field of ${op}`).toBeDefined();
      expect(matches(value, fields[key] ?? ""), `${op}.${key} is not ${fields[key]}`).toBe(true);
    }
    for (const [key, type] of Object.entries(fields)) {
      expect(key in given || type.endsWith("?"), `${op}.${key} is missing`).toBe(true);
    }
    const build = BUILDER_OF.get(op);
    expect(build, `no builder for ${op}`).toBeDefined();
    expect(build?.(fieldsOf(edit, op))).toStrictEqual(edit);
  });

  it("leave out an optional field they are not given", () => {
    expect(edits.addFormula(null)).toStrictEqual({ op: "add_formula", afterBeatId: null });
    expect(edits.addText("beat_1", "Hi")).toStrictEqual({ op: "add_text", afterBeatId: "beat_1", text: "Hi" });
    expect(edits.addBeat(null, "wait", [])).toStrictEqual({
      op: "add_beat",
      afterBeatId: null,
      action: "wait",
      targets: [],
    });
    expect(edits.setBeatAction("beat_2", "fade_out")).toStrictEqual({
      op: "set_beat_action",
      beatId: "beat_2",
      action: "fade_out",
    });
    expect(edits.addShape("circle", "beat_1")).toStrictEqual({
      op: "add_shape",
      kind: "circle",
      afterBeatId: "beat_1",
    });
    expect(edits.setBeatEasing("beat_1", null)).toStrictEqual({
      op: "set_beat_easing",
      beatId: "beat_1",
      easing: null,
    });
  });
});
