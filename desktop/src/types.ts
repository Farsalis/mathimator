import { parseExportProfile, type ExportProfile } from "./exportProfile";

export type { ExportProfile };

export type ObjectType =
  | "axes"
  | "function_plot"
  | "math_formula"
  | "text"
  | "three_d_axes"
  | "surface"
  | "parametric_curve";

export type SceneKind = "scene" | "three_d_scene";

export type ThreeDMode = "surface" | "curve";

export type BeatAction =
  | "create"
  | "fade_in"
  | "write"
  | "wait"
  | "transform_formula"
  | "set_color"
  | "move";

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
  code: string | null;
  focus?: {
    beatId: string | null;
    objectId: string | null;
  };
}

export type WorkerEvent =
  | { event: "ready"; jobId: string | null }
  | { event: "beat_ready"; jobId: string; beatIndex: number; path: string }
  | { event: "done"; jobId: string; path: string; current?: boolean }
  | { event: "error"; jobId: string | null; message: string }
  | { event: "rejected"; jobId: string | null; message: string };

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
const ACTIONS: readonly BeatAction[] = [
  "create",
  "fade_in",
  "write",
  "wait",
  "transform_formula",
  "set_color",
  "move",
];

export function parseEditorState(value: unknown): EditorState | null {
  if (!isRecord(value) || value.event !== "state") {
    return null;
  }
  const document = parseDocument(value.document);
  const derived = parseDerived(value.derived);
  if (!document || !derived || typeof value.projectPath !== "string") {
    return null;
  }
  const index = value.index === null ? null : parseIndex(value.index);
  if (value.index !== null && index === null) {
    return null;
  }
  const focus = parseFocus(value.focus);
  return {
    event: "state",
    projectPath: value.projectPath,
    document,
    derived,
    index,
    code: typeof value.code === "string" ? value.code : null,
    focus,
  };
}

export function parseWorkerEvent(value: unknown): WorkerEvent | null {
  if (!isRecord(value) || typeof value.event !== "string") {
    return null;
  }
  const event = value.event;
  if (event === "ready") {
    return { event: "ready", jobId: typeof value.jobId === "string" ? value.jobId : null };
  }
  if (
    event === "beat_ready" &&
    typeof value.jobId === "string" &&
    typeof value.beatIndex === "number" &&
    typeof value.path === "string"
  ) {
    return { event: "beat_ready", jobId: value.jobId, beatIndex: value.beatIndex, path: value.path };
  }
  if (event === "done" && typeof value.jobId === "string" && typeof value.path === "string") {
    return {
      event: "done",
      jobId: value.jobId,
      path: value.path,
      current: typeof value.current === "boolean" ? value.current : undefined,
    };
  }
  if (event === "error" && typeof value.message === "string") {
    return {
      event: "error",
      jobId: typeof value.jobId === "string" ? value.jobId : null,
      message: value.message,
    };
  }
  if (event === "rejected" && typeof value.message === "string") {
    return {
      event: "rejected",
      jobId: typeof value.jobId === "string" ? value.jobId : null,
      message: value.message,
    };
  }
  return null;
}

function parseDocument(value: unknown): ProjectDocument | null {
  if (!isRecord(value) || typeof value.name !== "string" || !isRecord(value.scene)) {
    return null;
  }
  const objects = parseObjects(value.scene.objects);
  const beats = parseBeats(value.scene.beats);
  const templateInstances = parseTemplates(value.scene.templateInstances);
  if (!objects || !beats || !templateInstances) {
    return null;
  }
  const kind = value.scene.kind === "three_d_scene" ? "three_d_scene" : value.scene.kind === "scene" ? "scene" : null;
  if (!kind) {
    return null;
  }
  const camera = value.scene.camera === undefined || value.scene.camera === null ? null : parseCamera(value.scene.camera);
  if (value.scene.camera !== undefined && value.scene.camera !== null && camera === null) {
    return null;
  }
  if (kind === "three_d_scene" && camera === null) {
    return null;
  }
  const exportProfile = parseExportProfile(value.exportProfile);
  if (!exportProfile) {
    return null;
  }
  return { name: value.name, scene: { kind, camera, objects, beats, templateInstances }, exportProfile };
}

function parseObjects(value: unknown): SceneObject[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const objects: SceneObject[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.id !== "string" || typeof item.name !== "string") {
      return null;
    }
    if (!isObjectType(item.type) || !isRecord(item.params)) {
      return null;
    }
    objects.push({ id: item.id, name: item.name, type: item.type, params: item.params });
  }
  return objects;
}

function parseBeats(value: unknown): Beat[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const beats: Beat[] = [];
  for (const item of value) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.duration !== "number" ||
      !isAction(item.action) ||
      !Array.isArray(item.targets) ||
      !item.targets.every((target) => typeof target === "string")
    ) {
      return null;
    }
    const beat: Beat = {
      id: item.id,
      duration: item.duration,
      action: item.action,
      targets: item.targets,
    };
    if (item.params !== undefined) {
      if (!isRecord(item.params)) {
        return null;
      }
      beat.params = item.params;
    }
    beats.push(beat);
  }
  return beats;
}

function parseCamera(value: unknown): Camera | null {
  if (!isRecord(value)) {
    return null;
  }
  if (typeof value.phi !== "number" || typeof value.theta !== "number" || typeof value.zoom !== "number") {
    return null;
  }
  return { phi: value.phi, theta: value.theta, zoom: value.zoom };
}

function parseTemplates(value: unknown): TemplateInstance[] | null {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    return null;
  }
  const templates: TemplateInstance[] = [];
  for (const item of value) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.templateId !== "string" ||
      !isRecord(item.params) ||
      !Array.isArray(item.objectIds) ||
      !item.objectIds.every((id) => typeof id === "string") ||
      !Array.isArray(item.beatIds) ||
      !item.beatIds.every((id) => typeof id === "string")
    ) {
      return null;
    }
    templates.push({
      id: item.id,
      templateId: item.templateId,
      params: item.params,
      objectIds: item.objectIds,
      beatIds: item.beatIds,
    });
  }
  return templates;
}

function parseDerived(value: unknown): EditorState["derived"] | null {
  if (!isRecord(value) || !isNumberList(value.starts) || !isBooleanList(value.dirty)) {
    return null;
  }
  if (typeof value.total !== "number" || typeof value.canUndo !== "boolean" || typeof value.canRedo !== "boolean") {
    return null;
  }
  return {
    starts: value.starts,
    total: value.total,
    dirty: value.dirty,
    canUndo: value.canUndo,
    canRedo: value.canRedo,
  };
}

function parseIndex(value: unknown): PreviewIndex | null {
  if (!isRecord(value) || !Array.isArray(value.beats)) {
    return null;
  }
  const beats: IndexBeat[] = [];
  for (const item of value.beats) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.index !== "number" ||
      typeof item.derivedStart !== "number" ||
      typeof item.duration !== "number" ||
      typeof item.fresh !== "boolean"
    ) {
      return null;
    }
    beats.push({
      id: item.id,
      index: item.index,
      derivedStart: item.derivedStart,
      duration: item.duration,
      partial: typeof item.partial === "string" ? item.partial : null,
      lastGood: typeof item.lastGood === "string" ? item.lastGood : null,
      fresh: item.fresh,
    });
  }
  return {
    output: typeof value.output === "string" ? value.output : null,
    beats,
  };
}

function parseFocus(value: unknown): EditorState["focus"] | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  return {
    beatId: typeof value.beatId === "string" ? value.beatId : null,
    objectId: typeof value.objectId === "string" ? value.objectId : null,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isObjectType(value: unknown): value is ObjectType {
  return typeof value === "string" && OBJECT_TYPES.some((item) => item === value);
}

function isAction(value: unknown): value is BeatAction {
  return typeof value === "string" && ACTIONS.some((item) => item === value);
}

function isNumberList(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => typeof item === "number");
}

function isBooleanList(value: unknown): value is boolean[] {
  return Array.isArray(value) && value.every((item) => typeof item === "boolean");
}
