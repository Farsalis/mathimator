/**
 * One builder per edit op (spec §6). tests/fixtures/protocol/edit-ops.json lists the ops, their fields and examples
 * the worker accepts; edits.test.ts checks every builder against it. A builder leaves out an optional field it is not
 * given, as the fixture's examples do.
 */
import type {
  Anchor,
  Axis,
  BeatAction,
  CameraKey,
  Easing,
  Edit,
  ExportProfile,
  LengthAxis,
  Params,
  ParamValue,
  ShapeType,
  ThreeDMode,
  Vector,
} from "./types";

/** The edit with the op `Op`. */
export type EditOf<Op extends Edit["op"]> = Extract<Edit, { op: Op }>;

export function setProjectName(name: string): EditOf<"set_project_name"> {
  return { op: "set_project_name", name };
}

export function setObjectName(objectId: string, name: string): EditOf<"set_object_name"> {
  return { op: "set_object_name", objectId, name };
}

export function setObjectColor(objectId: string, color: string): EditOf<"set_object_color"> {
  return { op: "set_object_color", objectId, color };
}

export function setExpression(objectId: string, expression: string): EditOf<"set_expression"> {
  return { op: "set_expression", objectId, expression };
}

export function setLatex(objectId: string, latex: string): EditOf<"set_latex"> {
  return { op: "set_latex", objectId, latex };
}

export function setText(objectId: string, text: string): EditOf<"set_text"> {
  return { op: "set_text", objectId, text };
}

export function setAnchor(objectId: string, anchor: Anchor): EditOf<"set_anchor"> {
  return { op: "set_anchor", objectId, anchor };
}

export function setObjectShift(objectId: string, shift: Vector): EditOf<"set_object_shift"> {
  return { op: "set_object_shift", objectId, shift };
}

export function setAxesRange(objectId: string, axis: Axis, value: [number, number, number]): EditOf<"set_axes_range"> {
  return { op: "set_axes_range", objectId, axis, value };
}

export function setLength(objectId: string, axis: LengthAxis, value: number): EditOf<"set_length"> {
  return { op: "set_length", objectId, axis, value };
}

export function setShowLabels(objectId: string, value: boolean): EditOf<"set_show_labels"> {
  return { op: "set_show_labels", objectId, value };
}

export function setDomain(objectId: string, xMin: number, xMax: number): EditOf<"set_domain"> {
  return { op: "set_domain", objectId, xMin, xMax };
}

export function setBeatDuration(beatId: string, duration: number): EditOf<"set_beat_duration"> {
  return { op: "set_beat_duration", beatId, duration };
}

/** Kept for older clients: `setBeatParam(beatId, "color", color)` on a set_color beat. */
export function setBeatColor(beatId: string, color: string): EditOf<"set_beat_color"> {
  return { op: "set_beat_color", beatId, color };
}

/** Kept for older clients: `setBeatParam(beatId, "shift", shift)` on a move beat. */
export function setBeatShift(beatId: string, shift: Vector): EditOf<"set_beat_shift"> {
  return { op: "set_beat_shift", beatId, shift };
}

export function addAxesFunction(afterBeatId: string | null): EditOf<"add_axes_function"> {
  return { op: "add_axes_function", afterBeatId };
}

export function addFormulaTransform(afterBeatId: string | null): EditOf<"add_formula_transform"> {
  return { op: "add_formula_transform", afterBeatId };
}

export function addThreeDAxes(afterBeatId: string | null): EditOf<"add_three_d_axes"> {
  return { op: "add_three_d_axes", afterBeatId };
}

export function addFormula(afterBeatId: string | null, latex?: string): EditOf<"add_formula"> {
  return { op: "add_formula", afterBeatId, ...(latex === undefined ? {} : { latex }) };
}

export function addText(afterBeatId: string | null, text?: string): EditOf<"add_text"> {
  return { op: "add_text", afterBeatId, ...(text === undefined ? {} : { text }) };
}

export function deleteObject(objectId: string): EditOf<"delete_object"> {
  return { op: "delete_object", objectId };
}

export function setThreeDMode(mode: ThreeDMode): EditOf<"set_three_d_mode"> {
  return { op: "set_three_d_mode", mode };
}

export function setCamera(key: CameraKey, value: number): EditOf<"set_camera"> {
  return { op: "set_camera", key, value };
}

export function setResolution(objectId: string, value: [number, number]): EditOf<"set_resolution"> {
  return { op: "set_resolution", objectId, value };
}

export function setCurveComponent(
  objectId: string,
  index: 0 | 1 | 2,
  expression: string,
): EditOf<"set_curve_component"> {
  return { op: "set_curve_component", objectId, index, expression };
}

export function setExportProfile({
  format,
  width,
  height,
  fps,
  transparent,
}: ExportProfile): EditOf<"set_export_profile"> {
  return { op: "set_export_profile", format, width, height, fps, transparent };
}

/** A beat of `action` after `afterBeatId`, or at the end; the worker fills in the settings `params` leaves out. */
export function addBeat(
  afterBeatId: string | null,
  action: BeatAction,
  targets: string[],
  params?: Params,
): EditOf<"add_beat"> {
  return { op: "add_beat", afterBeatId, action, targets, ...(params === undefined ? {} : { params }) };
}

/** Changes the kind of a beat; without `targets` the worker keeps its targets where the new kind can take them. */
export function setBeatAction(beatId: string, action: BeatAction, targets?: string[]): EditOf<"set_beat_action"> {
  return { op: "set_beat_action", beatId, action, ...(targets === undefined ? {} : { targets }) };
}

export function setBeatTargets(beatId: string, targets: string[]): EditOf<"set_beat_targets"> {
  return { op: "set_beat_targets", beatId, targets };
}

export function setBeatParam(beatId: string, key: string, value: ParamValue): EditOf<"set_beat_param"> {
  return { op: "set_beat_param", beatId, key, value };
}

/** Null goes back to the kind's default easing. */
export function setBeatEasing(beatId: string, easing: Easing | null): EditOf<"set_beat_easing"> {
  return { op: "set_beat_easing", beatId, easing };
}

export function deleteBeat(beatId: string): EditOf<"delete_beat"> {
  return { op: "delete_beat", beatId };
}

/** Moves a beat so that it ends up at `toIndex`. */
export function moveBeat(beatId: string, toIndex: number): EditOf<"move_beat"> {
  return { op: "move_beat", beatId, toIndex };
}

/** A shape and its entrance beat after `afterBeatId`, or at the end; `axesId` only for a point on axes. */
export function addShape(kind: ShapeType, afterBeatId: string | null, axesId?: string): EditOf<"add_shape"> {
  return { op: "add_shape", kind, afterBeatId, ...(axesId === undefined ? {} : { axesId }) };
}

export function setShapeParam(objectId: string, key: string, value: ParamValue): EditOf<"set_shape_param"> {
  return { op: "set_shape_param", objectId, key, value };
}
