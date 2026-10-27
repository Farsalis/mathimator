import {
  BEAT_ACTIONS,
  EASING_NAMES,
  isShapeType,
  KINDS,
  OBJECT_TYPES,
  SHAPES,
  type BeatAction,
  type Easing,
  type ObjectType,
  type ParamSpec,
  type Params,
  type ParamValue,
  type ShapeType,
  type Vector,
} from "./beatKinds";
import { asExportProfile, type ExportProfile } from "./exportProfile";
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
  ParseError,
  type Read,
} from "./parse";

export type { BeatAction, Easing, ExportProfile, ObjectType, Params, ParamValue, ShapeType, Vector };

export type SceneKind = "scene" | "three_d_scene";

export const THREE_D_MODES = ["surface", "curve"] as const;
export type ThreeDMode = (typeof THREE_D_MODES)[number];

export const ANCHORS = ["origin", "edge_up", "edge_down", "edge_left", "edge_right"] as const;
export type Anchor = (typeof ANCHORS)[number];

export const AXES = ["x", "y", "z"] as const;
export type Axis = (typeof AXES)[number];

/** The axes set_length accepts: only 2D axes have a length, along x and y. */
export const LENGTH_AXES = ["x", "y"] as const;
export type LengthAxis = (typeof LENGTH_AXES)[number];

export const CAMERA_KEYS = ["phi", "theta", "zoom"] as const;
export type CameraKey = (typeof CAMERA_KEYS)[number];

export interface SceneObject {
  id: string;
  name: string;
  type: ObjectType;
  /** A shape's settings are read by the catalog, so each has the type the catalog gives it. */
  params: Record<string, unknown>;
}

export interface Beat {
  id: string;
  duration: number;
  action: BeatAction;
  /** One or more objects for a kind of arity "some"; source then destination, or object then path, for a pair. */
  targets: string[];
  /** The settings of a kind that has them, read by the catalog; absent for every other kind. */
  params?: Params;
  /** Absent: the kind's default easing. */
  easing?: Easing;
}

export interface Camera {
  phi: number;
  theta: number;
  zoom: number;
}

export interface TemplateInstance {
  id: string;
  templateId: string;
  params: Record<string, unknown>;
  objectIds: string[];
  beatIds: string[];
}

export interface ProjectDocument {
  name: string;
  scene: {
    kind: SceneKind;
    camera: Camera | null;
    objects: SceneObject[];
    beats: Beat[];
    templateInstances: TemplateInstance[];
  };
  exportProfile: ExportProfile;
}

export interface IndexBeat {
  id: string;
  index: number;
  derivedStart: number;
  duration: number;
  partial: string | null;
  lastGood: string | null;
  fresh: boolean;
}

export interface PreviewIndex {
  output: string | null;
  beats: IndexBeat[];
}

export interface EditorState {
  event: "state";
  projectPath: string;
  document: ProjectDocument;
  derived: {
    starts: number[];
    total: number;
    dirty: boolean[];
    canUndo: boolean;
    canRedo: boolean;
    /** The object ids on screen before each beat, then after the last, in document order. */
    onScreen: string[][];
  };
  index: PreviewIndex | null;
  /** The generated scene.py, or null when codegen refused the document; `sourceError` then says why. */
  source: string | null;
  sourceError?: string;
  focus?: {
    beatId: string | null;
    objectId: string | null;
  };
  /** Edit replies only: false for an edit that changed nothing (D08). */
  changed?: boolean;
  /** Clear cache only: some files could not be removed. */
  warning?: { code: string; message: string };
}

/** The TeX check (C9.7). A failed check blocks formulas and axis labels (C14 Q4). */
export interface TexStatus {
  state: "checking" | "ok" | "failed";
  message: string | null;
}

/** A shell notice, shown once and kept by the shell until `dismiss_notice` (C9.5). */
export interface Notice {
  id: number;
  message: string;
}

/** The reply of `app_status` and `restart_worker` (C9.5). */
export interface AppStatus {
  worker: "ready" | "unavailable";
  /** Why the worker is unavailable. */
  reason: string | null;
  tex: TexStatus;
  /** Every notice not yet dismissed. */
  notices: Notice[];
}

export type WorkerEvent =
  | { event: "queued"; jobId: string; code: string; message: string }
  | { event: "started"; jobId: string }
  | { event: "beat_ready"; jobId: string; beatIndex: number; path: string }
  | { event: "done"; jobId: string; path: string; current?: boolean }
  | { event: "error"; jobId: string | null; code: string; message: string; details?: string }
  | { event: "rejected"; jobId: string; code: string; message: string; field?: string }
  /** The shell's line when the worker process ended without being asked to (C8.6). */
  | { event: "worker_exited"; message: string; exitCode: number | null }
  /** A notice the shell added after startup, for example after a respawn fell back to a new project (C9.5). */
  | ({ event: "notice" } & Notice)
  /** The TeX self-test finished (C9.7). */
  | { event: "tex_status"; state: "ok" | "failed"; message: string | null };

export type Edit =
  | { op: "set_project_name"; name: string }
  | { op: "set_object_name"; objectId: string; name: string }
  | { op: "set_object_color"; objectId: string; color: string }
  | { op: "set_expression"; objectId: string; expression: string }
  | { op: "set_latex"; objectId: string; latex: string }
  | { op: "set_text"; objectId: string; text: string }
  | { op: "set_anchor"; objectId: string; anchor: Anchor }
  | { op: "set_object_shift"; objectId: string; shift: [number, number] }
  | { op: "set_axes_range"; objectId: string; axis: Axis; value: [number, number, number] }
  | { op: "set_length"; objectId: string; axis: LengthAxis; value: number }
  | { op: "set_show_labels"; objectId: string; value: boolean }
  | { op: "set_domain"; objectId: string; xMin: number; xMax: number }
  | { op: "set_beat_duration"; beatId: string; duration: number }
  | { op: "set_beat_color"; beatId: string; color: string }
  | { op: "set_beat_shift"; beatId: string; shift: [number, number] }
  | { op: "add_axes_function"; afterBeatId: string | null }
  | { op: "add_formula_transform"; afterBeatId: string | null }
  | { op: "add_three_d_axes"; afterBeatId: string | null }
  | { op: "add_formula"; afterBeatId: string | null; latex?: string }
  | { op: "add_text"; afterBeatId: string | null; text?: string }
  | { op: "delete_object"; objectId: string }
  | { op: "set_three_d_mode"; mode: ThreeDMode }
  | { op: "set_camera"; key: CameraKey; value: number }
  | { op: "set_resolution"; objectId: string; value: [number, number] }
  | { op: "set_curve_component"; objectId: string; index: 0 | 1 | 2; expression: string }
  | {
      op: "set_export_profile";
      format: ExportProfile["format"];
      width: number;
      height: number;
      fps: number;
      transparent: boolean;
    }
  | { op: "add_beat"; afterBeatId: string | null; action: BeatAction; targets: string[]; params?: Params }
  /** Without `targets` the worker keeps the beat's targets where the new kind can take them. */
  | { op: "set_beat_action"; beatId: string; action: BeatAction; targets?: string[] }
  | { op: "set_beat_targets"; beatId: string; targets: string[] }
  | { op: "set_beat_param"; beatId: string; key: string; value: ParamValue }
  /** Null goes back to the kind's default easing. */
  | { op: "set_beat_easing"; beatId: string; easing: Easing | null }
  | { op: "delete_beat"; beatId: string }
  /** `toIndex` is the beat's index in the resulting list. */
  | { op: "move_beat"; beatId: string; toIndex: number }
  /** `axesId` names the axes of a point on axes, and is given for no other shape. */
  | { op: "add_shape"; kind: ShapeType; afterBeatId: string | null; axesId?: string }
  | { op: "set_shape_param"; objectId: string; key: string; value: ParamValue };

const SCENE_KINDS: readonly SceneKind[] = ["scene", "three_d_scene"];

/** Parses a `state` reply (C5.1). Throws ParseError naming the first field that does not match. */
export function parseEditorState(value: unknown): EditorState {
  const read = fieldsOf(value, "state");
  return {
    event: read("event", oneOf(["state"] as const)),
    projectPath: read("projectPath", asString),
    document: read("document", asDocument),
    derived: read("derived", asDerived),
    index: read("index", nullable(asIndex)),
    source: read("source", nullable(asString)),
    sourceError: read("sourceError", optional(asString)),
    focus: read("focus", optional(asFocus)),
    changed: read("changed", optional(asBoolean)),
    warning: read("warning", optional(asWarning)),
  };
}

export function parseAppStatus(value: unknown): AppStatus {
  const read = fieldsOf(value, "app_status");
  return {
    worker: read("worker", oneOf(["ready", "unavailable"] as const)),
    reason: read("reason", nullable(asString)),
    tex: read("tex", asTexStatus),
    notices: read("notices", listOf(asNotice)),
  };
}

/**
 * Parses a line from the `worker-event` channel. Returns null for an event the UI does not read (C2: unknown
 * events are ignored) and throws ParseError naming the field when a known event is malformed.
 */
export function parseWorkerEvent(value: unknown): WorkerEvent | null {
  if (!isRecord(value) || typeof value.event !== "string") {
    return null;
  }
  const read = fieldsOf(value, value.event);
  switch (value.event) {
    case "queued":
      return {
        event: "queued",
        jobId: read("jobId", asString),
        code: read("code", asString),
        message: read("message", asString),
      };
    case "started":
      return { event: "started", jobId: read("jobId", asString) };
    case "beat_ready":
      return {
        event: "beat_ready",
        jobId: read("jobId", asString),
        beatIndex: read("beatIndex", asInteger),
        path: read("path", asString),
      };
    case "done":
      return {
        event: "done",
        jobId: read("jobId", asString),
        path: read("path", asString),
        current: read("current", optional(asBoolean)),
      };
    case "error":
      return {
        event: "error",
        jobId: read("jobId", nullable(asString)),
        code: read("code", asString),
        message: read("message", asString),
        details: read("details", optional(asString)),
      };
    case "rejected":
      return {
        event: "rejected",
        jobId: read("jobId", asString),
        code: read("code", asString),
        message: read("message", asString),
        field: read("field", optional(asString)),
      };
    case "worker_exited":
      return {
        event: "worker_exited",
        message: read("message", asString),
        exitCode: read("exitCode", nullable(asInteger)),
      };
    case "notice":
      return { event: "notice", ...asNotice(value, "notice") };
    case "tex_status":
      return {
        event: "tex_status",
        state: read("state", oneOf(["ok", "failed"] as const)),
        message: read("message", nullable(asString)),
      };
    default:
      return null;
  }
}

function asDocument(value: unknown, path: string): ProjectDocument {
  const read = fieldsOf(value, path);
  return {
    name: read("name", asString),
    scene: read("scene", asScene),
    exportProfile: read("exportProfile", asExportProfile),
  };
}

function asScene(value: unknown, path: string): ProjectDocument["scene"] {
  const read = fieldsOf(value, path);
  const kind = read("kind", oneOf(SCENE_KINDS));
  const camera = read("camera", optional(nullable(asCamera))) ?? null;
  if (kind === "three_d_scene" && camera === null) {
    throw new ParseError(`${path}.camera`, "must be set in a 3D scene");
  }
  return {
    kind,
    camera,
    objects: read("objects", listOf(asObject)),
    beats: read("beats", listOf(asBeat)),
    templateInstances: read("templateInstances", optional(listOf(asTemplate))) ?? [],
  };
}

function asObject(value: unknown, path: string): SceneObject {
  const read = fieldsOf(value, path);
  const id = read("id", asString);
  const name = read("name", asString);
  const type = read("type", oneOf(OBJECT_TYPES));
  return { id, name, type, params: read("params", isShapeType(type) ? asParams(SHAPES[type].params) : asRecord) };
}

function asBeat(value: unknown, path: string): Beat {
  const read = fieldsOf(value, path);
  const id = read("id", asString);
  const duration = read("duration", asNumber);
  const action = read("action", oneOf(BEAT_ACTIONS));
  const { params } = KINDS[action];
  return {
    id,
    duration,
    action,
    targets: read("targets", listOf(asString)),
    params: params.length > 0 ? read("params", asParams(params)) : undefined,
    easing: read("easing", optional(oneOf(EASING_NAMES))),
  };
}

const PARAM_READERS: Record<ParamSpec["type"], Read<ParamValue>> = {
  number: asNumber,
  vector: pairOf(asNumber),
  color: asString,
  id: asString,
};

/** Reads each setting `specs` lists by its type, and leaves out any other key. */
function asParams(specs: readonly ParamSpec[]): Read<Params> {
  return (value, path) => {
    const read = fieldsOf(value, path);
    return Object.fromEntries(specs.map((spec) => [spec.name, read(spec.name, PARAM_READERS[spec.type])]));
  };
}

function asCamera(value: unknown, path: string): Camera {
  const read = fieldsOf(value, path);
  return { phi: read("phi", asNumber), theta: read("theta", asNumber), zoom: read("zoom", asNumber) };
}

function asTemplate(value: unknown, path: string): TemplateInstance {
  const read = fieldsOf(value, path);
  return {
    id: read("id", asString),
    templateId: read("templateId", asString),
    params: read("params", asRecord),
    objectIds: read("objectIds", listOf(asString)),
    beatIds: read("beatIds", listOf(asString)),
  };
}

function asDerived(value: unknown, path: string): EditorState["derived"] {
  const read = fieldsOf(value, path);
  return {
    starts: read("starts", listOf(asNumber)),
    total: read("total", asNumber),
    dirty: read("dirty", listOf(asBoolean)),
    canUndo: read("canUndo", asBoolean),
    canRedo: read("canRedo", asBoolean),
    onScreen: read("onScreen", listOf(listOf(asString))),
  };
}

function asIndex(value: unknown, path: string): PreviewIndex {
  const read = fieldsOf(value, path);
  return { output: read("output", nullable(asString)), beats: read("beats", listOf(asIndexBeat)) };
}

function asIndexBeat(value: unknown, path: string): IndexBeat {
  const read = fieldsOf(value, path);
  return {
    id: read("id", asString),
    index: read("index", asInteger),
    derivedStart: read("derivedStart", asNumber),
    duration: read("duration", asNumber),
    partial: read("partial", nullable(asString)),
    lastGood: read("lastGood", nullable(asString)),
    fresh: read("fresh", asBoolean),
  };
}

function asFocus(value: unknown, path: string): NonNullable<EditorState["focus"]> {
  const read = fieldsOf(value, path);
  return { beatId: read("beatId", nullable(asString)), objectId: read("objectId", nullable(asString)) };
}

function asTexStatus(value: unknown, path: string): TexStatus {
  const read = fieldsOf(value, path);
  return {
    state: read("state", oneOf(["checking", "ok", "failed"] as const)),
    message: read("message", nullable(asString)),
  };
}

function asNotice(value: unknown, path: string): Notice {
  const read = fieldsOf(value, path);
  return { id: read("id", asInteger), message: read("message", asString) };
}

function asWarning(value: unknown, path: string): NonNullable<EditorState["warning"]> {
  const read = fieldsOf(value, path);
  return { code: read("code", asString), message: read("message", asString) };
}
