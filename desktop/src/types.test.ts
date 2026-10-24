import { describe, expect, it } from "vitest";
import { ParseError } from "./parse";
import { parseEditorState, parseWorkerEvent } from "./types";

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
        objects: [{ id: "plot_1", name: "Plot", type: "function_plot", params: { expression: "sin(x)" } }],
        beats: [{ id: "beat_1", duration: 2, action: "create", targets: ["plot_1"] }],
        templateInstances: [],
      },
      exportProfile: { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false },
    },
    derived: { starts: [0], total: 2, dirty: [true], canUndo: true, canRedo: false },
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

  it.each([
    ["state.event", "code"],
    ["state.projectPath", undefined],
    ["state.source", undefined],
    ["state.document.scene.objects[0].type", "cube"],
    ["state.document.scene.beats[0].targets[0]", 7],
    ["state.document.exportProfile.fps", 29.97],
    ["state.derived.starts[0]", "0"],
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

  it("ignores events the UI does not read", () => {
    expect(parseWorkerEvent(stateReply())).toBeNull();
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
  ])("names %s in a malformed event", (field, event) => {
    expect(failingField(() => parseWorkerEvent(event))).toBe(field);
  });
});
