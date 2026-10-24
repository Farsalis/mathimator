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
  ParseError,
} from "./parse";

export type { ExportProfile };

export type ObjectType =
  "axes" | "function_plot" | "math_formula" | "text" | "three_d_axes" | "surface" | "parametric_curve";

export type SceneKind = "scene" | "three_d_scene";

export type ThreeDMode = "surface" | "curve";

export type BeatAction = "create" | "fade_in" | "write" | "wait" | "transform_formula" | "set_color" | "move";

export type Anchor = "origin" | "edge_up" | "edge_down" | "edge_left" | "edge_right";

export interface SceneObject {
  id: string;
  name: string;
  type: ObjectType;
  params: Record<string, unknown>;
}

export interface Beat {
  id: string;
  duration: number;
  action: BeatAction;
  targets: string[];
  params?: Record<string, unknown>;
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

export type WorkerEvent =
  | { event: "ready"; jobId: string }
  | { event: "queued"; jobId: string; code: string; message: string }
  | { event: "started"; jobId: string }
  | { event: "beat_ready"; jobId: string; beatIndex: number; path: string }
  | { event: "done"; jobId: string; path: string; current?: boolean }
  | { event: "error"; jobId: string | null; code: string; message: string; details?: string }
  | { event: "rejected"; jobId: string; code: string; message: string; field?: string }
  /** The shell's line when the worker process ended without being asked to (C8.6). */
  | { event: "worker_exited"; message: string; exitCode: number | null };

export type Edit =
  | { op: "set_project_name"; name: string }
  | { op: "set_object_name"; objectId: string; name: string }
  | { op: "set_object_color"; objectId: string; color: string }
  | { op: "set_expression"; objectId: string; expression: string }
  | { op: "set_latex"; objectId: string; latex: string }
  | { op: "set_text"; objectId: string; text: string }
  | { op: "set_anchor"; objectId: string; anchor: Anchor }
  | { op: "set_object_shift"; objectId: string; shift: [number, number] }
  | { op: "set_axes_range"; objectId: string; axis: "x" | "y" | "z"; value: [number, number, number] }
  | { op: "set_length"; objectId: string; axis: "x" | "y"; value: number }
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
  | { op: "set_camera"; key: "phi" | "theta" | "zoom"; value: number }
  | { op: "set_resolution"; objectId: string; value: [number, number] }
  | { op: "set_curve_component"; objectId: string; index: 0 | 1 | 2; expression: string }
  | {
      op: "set_export_profile";
      format: ExportProfile["format"];
      width: number;
      height: number;
      fps: number;
      transparent: boolean;
    };

const OBJECT_TYPES: readonly ObjectType[] = [
  "axes",
  "function_plot",
  "math_formula",
  "text",
  "three_d_axes",
  "surface",
  "parametric_curve",
];
const ACTIONS: readonly BeatAction[] = ["create", "fade_in", "write", "wait", "transform_formula", "set_color", "move"];
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
    case "ready":
      return { event: "ready", jobId: read("jobId", asString) };
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
  return {
    id: read("id", asString),
    name: read("name", asString),
    type: read("type", oneOf(OBJECT_TYPES)),
    params: read("params", asRecord),
  };
}

function asBeat(value: unknown, path: string): Beat {
  const read = fieldsOf(value, path);
  return {
    id: read("id", asString),
    duration: read("duration", asNumber),
    action: read("action", oneOf(ACTIONS)),
    targets: read("targets", listOf(asString)),
    params: read("params", optional(asRecord)),
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

function asWarning(value: unknown, path: string): NonNullable<EditorState["warning"]> {
  const read = fieldsOf(value, path);
  return { code: read("code", asString), message: read("message", asString) };
}
