import { describe, expect, it } from "vitest";
import { BEAT_ACTIONS, EASING_NAMES, SHAPE_TYPES } from "./beatKinds";
import { asRecord, fieldsOf, listOf, ParseError } from "./parse";
import { parseAppStatus, parseEditorState, parseWorkerEvent } from "./types";

/**
 * The documents of tests/fixtures/render-smoke, which the worker accepts and renders, by path. Like the sources of
 * edits.test.ts, the glob takes no type argument, so that vitest's module cache leaves this test out and sees a new
 * document.
 */
const RENDER_SMOKE = import.meta.glob("../../tests/fixtures/render-smoke/*/project.json", {
  eager: true,
  import: "default",
});

function stateReply() {
  return {
    event: "state",
    jobId: "rpc-3",
    projectPath: "C:/project",
    document: {
      schemaVersion: 1,
      name: "Sine",
      scene: {
        id: "scene_1",
        kind: "scene",
        objects: [
          { id: "plot_1", name: "Plot", type: "function_plot", params: { expression: "sin(x)" } },
          { id: "dot_1", name: "Dot", type: "dot", params: { center: [0, 0], radius: 0.08, color: "#E07A5F" } },
        ],
        beats: [
          { id: "beat_1", duration: 2, action: "create", targets: ["plot_1"] },
          { id: "beat_2", duration: 1, action: "grow", targets: ["dot_1"], easing: "rush_into" },
          { id: "beat_3", duration: 1, action: "scale", targets: ["plot_1", "dot_1"], params: { factor: 2 } },
        ],
        templateInstances: [],
      },
      exportProfile: { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false },
    },
    derived: {
      starts: [0, 2, 3],
      total: 4,
      dirty: [true, true, true],
      canUndo: true,
      canRedo: false,
      onScreen: [[], ["plot_1"], ["plot_1", "dot_1"], ["plot_1", "dot_1"]],
    },
    index: {
      output: null,
      beats: [{ id: "beat_1", index: 0, derivedStart: 0, duration: 2, partial: null, lastGood: null, fresh: false }],
    },
    source: "from manim import *\n",
  };
}

/** A state reply with the value at `field`, a path such as `state.derived.starts[1]`, replaced by `value`. */
function stateWith(field: string, value: unknown): object {
  const keys = field
    .replace(/\[(\d+)\]/g, ".$1")
    .split(".")
    .slice(1);
  const last = keys.pop() ?? "";
  const reply = stateReply();
  const parent = keys.reduce<object>((node, key) => Reflect.get(node, key) as object, reply);
  Reflect.set(parent, last, value);
  return reply;
}

function failingField(read: () => unknown): string {
  try {
    read();
  } catch (reason: unknown) {
    if (reason instanceof ParseError) {
      return reason.field;
    }
    throw reason;
  }
  throw new Error("the reply parsed");
}

describe("parseEditorState", () => {
  it("reads an edit reply with focus and changed", () => {
    const state = parseEditorState({ ...stateReply(), focus: { beatId: null, objectId: "plot_1" }, changed: false });
    expect(state.projectPath).toBe("C:/project");
    expect(state.source).toBe("from manim import *\n");
    expect(state.focus).toEqual({ beatId: null, objectId: "plot_1" });
    expect(state.changed).toBe(false);
    expect(state.document.scene.beats[0]?.targets).toEqual(["plot_1"]);
    expect(state.document.scene.camera).toBeNull();
  });

  it("reads sourceError and the clear-cache warning", () => {
    const warning = { code: "files_in_use", message: "Some cache files are in use and were not removed." };
    const state = parseEditorState({ ...stateReply(), source: null, sourceError: "eq_1 left the screen", warning });
    expect(state.source).toBeNull();
    expect(state.sourceError).toBe("eq_1 left the screen");
    expect(state.warning).toEqual(warning);
  });

  it("reads a 3D scene with its camera and treats missing template instances as none", () => {
    const reply = stateReply();
    const camera = { phi: 1.1, theta: -0.8, zoom: 1 };
    const scene = { kind: "three_d_scene", camera, objects: [], beats: [] };
    const state = parseEditorState({ ...reply, document: { ...reply.document, scene } });
    expect(state.document.scene.camera).toEqual(camera);
    expect(state.document.scene.templateInstances).toEqual([]);
  });

  it("reads the easing, the settings and the several targets of beats, and what is on screen around them", () => {
    const state = parseEditorState(stateReply());
    const [create, grow, scale] = state.document.scene.beats;
    expect(create).toMatchObject({ action: "create", params: undefined, easing: undefined });
    expect(grow).toMatchObject({ action: "grow", easing: "rush_into" });
    expect(scale).toMatchObject({ action: "scale", targets: ["plot_1", "dot_1"], params: { factor: 2 } });
    expect(state.derived.onScreen).toEqual([[], ["plot_1"], ["plot_1", "dot_1"], ["plot_1", "dot_1"]]);
  });

  it("reads a shape's settings by the catalog and keeps every other object's as they are", () => {
    const reply = stateReply();
    const point = {
      id: "point_1",
      name: "Point",
      type: "axes_point",
      params: { axesId: "axes_1", x: 1.5, y: -2, radius: 0.08, color: "#E07A5F", extra: true },
    };
    const scene = { ...reply.document.scene, objects: [...reply.document.scene.objects, point] };
    const objects = parseEditorState({ ...reply, document: { ...reply.document, scene } }).document.scene.objects;
    expect(objects.map((item) => item.params)).toEqual([
      { expression: "sin(x)" },
      { center: [0, 0], radius: 0.08, color: "#E07A5F" },
      { axesId: "axes_1", x: 1.5, y: -2, radius: 0.08, color: "#E07A5F" },
    ]);
  });

  it("reads a camera move with its settings and easing", () => {
    const reply = stateReply();
    const camera = { phi: 60, theta: -45, zoom: 1 };
    const beat = {
      id: "beat_1",
      duration: 2,
      action: "move_camera",
      targets: [],
      params: { phi: 75, theta: 45, zoom: 0.8 },
      easing: "there_and_back",
    };
    const scene = { kind: "three_d_scene", camera, objects: [], beats: [beat] };
    const state = parseEditorState({ ...reply, document: { ...reply.document, scene } });
    expect(state.document.scene.beats).toEqual([beat]);
    const missing = { ...beat, params: { phi: 75, theta: 45 } };
    const broken = { ...reply, document: { ...reply.document, scene: { ...scene, beats: [missing] } } };
    expect(failingField(() => parseEditorState(broken))).toBe("state.document.scene.beats[0].params.zoom");
  });

  it("reads the objects and beats of every render-smoke document whole, every kind, easing and shape among them", () => {
    const actions = new Set<string>();
    const easings = new Set<string>();
    const types = new Set<string>();
    for (const [path, document] of Object.entries(RENDER_SMOKE)) {
      const scene = fieldsOf(fieldsOf(document, path)("scene", asRecord), `${path}.scene`);
      const parsed = parseEditorState({ ...stateReply(), document }).document.scene;
      expect(parsed.objects, path).toEqual(scene("objects", listOf(asRecord)));
      expect(parsed.beats, path).toEqual(scene("beats", listOf(asRecord)));
      for (const beat of parsed.beats) {
        actions.add(beat.action);
        if (beat.easing !== undefined) {
          easings.add(beat.easing);
        }
      }
      for (const object of parsed.objects) {
        types.add(object.type);
      }
    }
    expect([...actions].sort()).toEqual([...BEAT_ACTIONS].sort());
    expect([...easings].sort()).toEqual([...EASING_NAMES].sort());
    expect(SHAPE_TYPES.filter((type) => !types.has(type))).toEqual([]);
  });

  it.each([
    ["state.event", "code"],
    ["state.projectPath", undefined],
    ["state.source", undefined],
    ["state.document.scene.objects[0].type", "cube"],
    ["state.document.scene.objects[1].params.center", [0, 0, 0]],
    ["state.document.scene.objects[1].params.center[1]", "0"],
    ["state.document.scene.objects[1].params.radius", undefined],
    ["state.document.scene.objects[1].params.color", 0xe07a5f],
    ["state.document.scene.beats[0].targets[0]", 7],
    ["state.document.scene.beats[1].action", "bounce"],
    ["state.document.scene.beats[1].easing", "bounce"],
    ["state.document.scene.beats[2].params", undefined],
    ["state.document.scene.beats[2].params.factor", "2"],
    ["state.document.exportProfile.fps", 29.97],
    ["state.derived.starts[0]", "0"],
    ["state.derived.onScreen", undefined],
    ["state.derived.onScreen[2][1]", 7],
    ["state.index.beats[0].fresh", undefined],
    ["state.index.beats[0].partial", 3],
  ])("names %s when it does not match", (field, value) => {
    expect(failingField(() => parseEditorState(stateWith(field, value)))).toBe(field);
  });

  it("names the camera of a 3D scene that has none", () => {
    const reply = stateWith("state.document.scene.kind", "three_d_scene");
    expect(failingField(() => parseEditorState(reply))).toBe("state.document.scene.camera");
  });

  it("names a missing field inside the warning", () => {
    const reply = { ...stateReply(), warning: { code: "files_in_use" } };
    expect(failingField(() => parseEditorState(reply))).toBe("state.warning.message");
  });

  it("puts the field in the message", () => {
    expect(() => parseEditorState(stateWith("state.derived.canUndo", "yes"))).toThrow(
      "Unexpected reply from the worker: state.derived.canUndo must be true or false.",
    );
  });
});

describe("parseWorkerEvent", () => {
  it("reads the protocol-2 job events", () => {
    expect(parseWorkerEvent({ event: "started", jobId: "export-2" })).toEqual({ event: "started", jobId: "export-2" });
    const queued = {
      event: "queued",
      jobId: "preview-3",
      code: "stopping_previous",
      message: "Waiting for the previous render to stop.",
    };
    expect(parseWorkerEvent(queued)).toEqual(queued);
    expect(parseWorkerEvent({ event: "done", jobId: "preview-3", path: "C:/p/preview.mp4" })).toEqual({
      event: "done",
      jobId: "preview-3",
      path: "C:/p/preview.mp4",
      current: undefined,
    });
  });

  it("reads codes, details and field on failures and refusals", () => {
    const failed = {
      event: "error",
      jobId: "export-4",
      code: "render_failed",
      message: "manim exited with status 1: ValueError: bad",
      details: "line 1\nline 2",
    };
    expect(parseWorkerEvent(failed)).toEqual(failed);
    expect(parseWorkerEvent({ event: "error", jobId: null, code: "bad_request", message: "missing id" })).toMatchObject(
      {
        jobId: null,
        code: "bad_request",
        details: undefined,
      },
    );
    const refused = {
      event: "rejected",
      jobId: "rpc-5",
      code: "invalid_text",
      message: "latex contains a character Mathimator cannot store.",
      field: "latex",
    };
    expect(parseWorkerEvent(refused)).toEqual(refused);
  });

  it("reads the shell's worker_exited line", () => {
    const exited = { event: "worker_exited", jobId: null, message: "The worker stopped (exit code 1).", exitCode: 1 };
    expect(parseWorkerEvent(exited)).toEqual({
      event: "worker_exited",
      message: "The worker stopped (exit code 1).",
      exitCode: 1,
    });
    expect(parseWorkerEvent({ ...exited, exitCode: null })).toMatchObject({ exitCode: null });
  });

  it("reads the shell's notice and tex_status lines", () => {
    const notice = {
      event: "notice",
      jobId: null,
      id: 3,
      message: "Could not reopen C:/old. A new project was started.",
    };
    expect(parseWorkerEvent(notice)).toEqual({ event: "notice", id: 3, message: notice.message });
    const tex = { event: "tex_status", jobId: null, state: "failed", message: "Reinstall Mathimator for all users." };
    expect(parseWorkerEvent(tex)).toEqual({ event: "tex_status", state: "failed", message: tex.message });
  });

  it("ignores events the UI does not read", () => {
    expect(parseWorkerEvent(stateReply())).toBeNull();
    expect(parseWorkerEvent({ event: "ready", jobId: "status-1", protocol: 2, startupError: null })).toBeNull();
    expect(parseWorkerEvent({ event: "code", jobId: "rpc-1", source: "" })).toBeNull();
    expect(parseWorkerEvent({ jobId: "rpc-1" })).toBeNull();
    expect(parseWorkerEvent("ready")).toBeNull();
  });

  it.each([
    ["beat_ready.beatIndex", { event: "beat_ready", jobId: "preview-1", beatIndex: 1.5, path: "C:/p/a.mp4" }],
    ["done.current", { event: "done", jobId: "export-1", path: "C:/p/e.mp4", current: "yes" }],
    ["error.code", { event: "error", jobId: "preview-1", message: "cancelled" }],
    ["rejected.jobId", { event: "rejected", jobId: null, code: "no_such_job", message: "No render with that id." }],
    ["started.jobId", { event: "started" }],
    ["worker_exited.exitCode", { event: "worker_exited", jobId: null, message: "The worker stopped." }],
    ["notice.id", { event: "notice", jobId: null, id: "1", message: "Hello." }],
    ["tex_status.state", { event: "tex_status", jobId: null, state: "checking", message: null }],
  ])("names %s in a malformed event", (field, event) => {
    expect(failingField(() => parseWorkerEvent(event))).toBe(field);
  });
});

describe("parseAppStatus", () => {
  const status = {
    worker: "unavailable",
    reason: "The installed runtime is incomplete (python). Reinstall Mathimator.",
    tex: { state: "checking", message: null },
    notices: [{ id: 1, message: "Could not reopen C:/old. A new project was started." }],
  };

  it("reads the worker, the reason, the TeX state and the notices (C9.5)", () => {
    expect(parseAppStatus(status)).toEqual(status);
  });

  it.each([
    ["app_status.worker", { ...status, worker: "starting" }],
    ["app_status.tex.state", { ...status, tex: { state: "maybe", message: null } }],
    ["app_status.notices[0].message", { ...status, notices: [{ id: 1 }] }],
  ])("names %s when it is malformed", (field, value) => {
    expect(failingField(() => parseAppStatus(value))).toBe(field);
  });
});
