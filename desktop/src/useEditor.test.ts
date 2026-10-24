// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExportProfile } from "./exportProfile";
import { PREVIEW_DEBOUNCE_MS, useEditor } from "./useEditor";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(() => Promise.resolve(() => undefined)) }));

const invokeMock = vi.mocked(invoke);
const listenMock = vi.mocked(listen);

const TIMEOUT = { code: "timeout", message: "No reply within 30 s." };
const TIMEOUT_TEXT = "The worker did not answer in time.";
const RESTART_NOTE = "The worker restarted. The document was reloaded from disk and undo history was cleared.";
const PROFILE: ExportProfile = { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false };
const GIF: ExportProfile = { format: "gif", width: 854, height: 480, fps: 15, transparent: false };
const DURATION = { op: "set_beat_duration", beatId: "beat_1", duration: 3 } as const;
const EXIT = { event: "worker_exited", jobId: null, message: "The worker stopped (exit code 1).", exitCode: 1 };
const EXITED = { code: "worker_exited", message: "The worker stopped (exit code 1)." };

/** A scripted reply that rejects with `reason`. */
class Rejection {
  constructor(readonly reason: unknown) {}
}

/**
 * Scripts the shell: each invoke takes the next reply listed for its command, and a Rejection rejects. The queue sends
 * one request at a time, so the replies of one command are taken in the order the UI sends them.
 */
function script(replies: Record<string, unknown[]>): void {
  invokeMock.mockImplementation((command) => {
    const reply = replies[command]?.shift();
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- invoke rejects with a ShellError
    return reply instanceof Rejection ? Promise.reject(reply.reason) : Promise.resolve(reply);
  });
}

function deferred(): {
  promise: Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
} {
  let resolve: (value: unknown) => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<unknown>((settle, fail) => {
    resolve = settle;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function commands(): string[] {
  return invokeMock.mock.calls.map(([command]) => command);
}

function callsOf(command: string): number {
  return commands().filter((name) => name === command).length;
}

/** Sends a line to the hook's `worker-event` listener. */
function emit(payload: unknown): void {
  const handler = listenMock.mock.lastCall?.[1];
  if (!handler) {
    throw new Error("The hook is not listening to worker-event.");
  }
  act(() => handler({ event: "worker-event", id: 1, payload }));
}

/** Waits past the preview debounce, so a scheduled preview would have been sent by now. */
async function afterDebounce(): Promise<void> {
  await act(() => new Promise((resolve) => setTimeout(resolve, PREVIEW_DEBOUNCE_MS + 100)));
}

function stateReply(projectPath: string, name = "Sine", exportProfile = PROFILE) {
  return {
    event: "state",
    jobId: "rpc-1",
    projectPath,
    document: {
      name,
      scene: {
        kind: "scene",
        objects: [{ id: "plot_1", name: "Plot", type: "function_plot", params: {} }],
        beats: [{ id: "beat_1", duration: 2, action: "create", targets: ["plot_1"] }],
        templateInstances: [],
      },
      exportProfile,
    },
    derived: { starts: [0], total: 2, dirty: [true], canUndo: false, canRedo: false },
    index: null,
    source: "",
  };
}

/** The reply to an edit that changed nothing, such as the Export task's unchanged profile (D08). */
function unchanged(projectPath: string) {
  return { ...stateReply(projectPath), changed: false };
}

/** A state reply whose preview index holds beat 0 with `partial`, not fresh. */
function indexedReply(partial: string | null) {
  const beat = { id: "beat_1", index: 0, derivedStart: 0, duration: 2, partial, lastGood: null, fresh: false };
  return { ...stateReply("C:/first"), index: { output: null, beats: [beat] } };
}

function refusal(code: string, message: string) {
  return { event: "rejected", jobId: "rpc-2", code, message };
}

function cancelled(jobId: string) {
  return { event: "error", jobId, code: "cancelled", message: "cancelled" };
}

async function started(): Promise<{ current: ReturnType<typeof useEditor> }> {
  const { result } = renderHook(() => useEditor());
  await waitFor(() => expect(result.current.state).not.toBeNull());
  return result;
}

/** Starts the editor and sends an export that the shell accepts. */
async function exported(result: { current: ReturnType<typeof useEditor> }): Promise<void> {
  act(() => result.current.onStartExport(PROFILE, false));
  await waitFor(() => expect(callsOf("export_movie")).toBe(1));
}

/**
 * Starts the editor on `first`, exports a movie, moves the view, opens View code and exports the code, then sends an
 * edit that times out. The resync's get_state answers with `resynced`, whose document is named "Resynced".
 */
async function editAfterTimeout(first: string, resynced: string): Promise<ReturnType<typeof useEditor>> {
  script({
    get_state: [stateReply(first), stateReply(resynced, "Resynced")],
    worker_status: [null],
    reserve_job_id: ["export-1"],
    edit_document: [unchanged(first), new Rejection(TIMEOUT)],
    export_movie: [null],
    export_code_dialog: [{ path: `${first}/scene.py` }],
  });
  const result = await started();
  await exported(result);
  emit({ event: "done", jobId: "export-1", path: `${first}/scene.mp4`, current: true });
  act(() => {
    result.current.setSelection({ kind: "object", id: "plot_1" });
    result.current.setPlayhead(1.5);
    result.current.setPlaying(true);
    result.current.setCodeOpen(true);
    result.current.onDraftDuration("beat_1", 3);
  });
  await act(() => result.current.onExportCode());
  await act(() => result.current.commit({ op: "set_project_name", name: "Late" }, false));
  await waitFor(() => expect(result.current.state?.document.name).toBe("Resynced"));
  expect(commands()).toEqual([
    "get_state",
    "worker_status",
    "reserve_job_id",
    "edit_document",
    "export_movie",
    "export_code_dialog",
    "edit_document",
    "get_state",
  ]);
  return result.current;
}

afterEach(() => {
  cleanup();
  invokeMock.mockReset();
  listenMock.mockClear();
});

describe("useEditor startup", () => {
  it("asks for ready and recovers through the resync when the startup get_state times out", async () => {
    script({ get_state: [new Rejection(TIMEOUT), stateReply("C:/first")], worker_status: [null] });
    const { result } = renderHook(() => useEditor());
    await waitFor(() => expect(callsOf("worker_status")).toBe(1));
    emit({ event: "ready", jobId: "status-1" });
    await waitFor(() => expect(result.current.state?.projectPath).toBe("C:/first"));
    expect(commands().slice(0, 3)).toEqual(["get_state", "worker_status", "get_state"]);
    expect(result.current.ready).toBe(true);
    expect(result.current.status).toBe("Ready");
    expect(result.current.error).toBe(TIMEOUT_TEXT);
  });
});

describe("useEditor timeout resync", () => {
  it("runs the Open reset when the resync shows another folder", async () => {
    const editor = await editAfterTimeout("C:/first", "C:/second");
    expect(editor.state?.projectPath).toBe("C:/second");
    expect(editor.error).toBe(TIMEOUT_TEXT);
    expect(editor.selection).toEqual({ kind: "none" });
    expect(editor.playhead).toBe(0);
    expect(editor.playing).toBe(false);
    expect(editor.codeOpen).toBe(false);
    expect(editor.drafts).toEqual({});
    expect(editor.note).toBeNull();
    expect(editor.exportPath).toBeNull();
  });

  it("keeps the view when the resync shows the same folder", async () => {
    const editor = await editAfterTimeout("C:/first", "C:/first");
    expect(editor.state?.projectPath).toBe("C:/first");
    expect(editor.error).toBe(TIMEOUT_TEXT);
    expect(editor.selection).toEqual({ kind: "object", id: "plot_1" });
    expect(editor.playhead).toBe(1.5);
    expect(editor.playing).toBe(true);
    expect(editor.codeOpen).toBe(true);
    expect(editor.drafts).toEqual({ beat_1: 3 });
    expect(editor.note).toBe("Code exported to C:/first/scene.py");
    expect(editor.exportPath).toBe("C:/first/scene.mp4");
  });
});

describe("useEditor previews", () => {
  it("ends a preview whose render_preview invoke times out", async () => {
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/first")],
      worker_status: [null],
      edit_document: [stateReply("C:/first")],
      reserve_job_id: ["preview-1"],
      render_preview: [new Rejection(TIMEOUT)],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("get_state")).toBe(2));
    await act(() => Promise.resolve());
    expect(commands()).toEqual([
      "get_state",
      "worker_status",
      "edit_document",
      "reserve_job_id",
      "render_preview",
      "get_state",
    ]);
    expect(result.current.status).toBe("Ready");
    expect(result.current.error).toBe(TIMEOUT_TEXT);
  });

  it.each(["onOpen", "onOpenSample", "onClearCache"] as const)("starts one preview after %s", async (action) => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      open_project_dialog: [stateReply("C:/second")],
      open_sample: [stateReply("C:/sample")],
      clear_cache: [stateReply("C:/first")],
      reserve_job_id: ["preview-1"],
      render_preview: [null],
    });
    const result = await started();
    await act(() => result.current[action]());
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    await afterDebounce();
    expect(callsOf("render_preview")).toBe(1);
    expect(invokeMock).toHaveBeenLastCalledWith("render_preview", { jobId: "preview-1" });
    expect(result.current.status).toBe("Rendering preview");
  });

  it("starts none after undo, redo, a no-op edit or an edit that leaves no beats", async () => {
    const empty = stateReply("C:/first");
    empty.document.scene = { ...empty.document.scene, objects: [], beats: [] };
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      undo_document: [stateReply("C:/first")],
      redo_document: [stateReply("C:/first")],
      edit_document: [unchanged("C:/first"), { ...empty, changed: true }],
    });
    const result = await started();
    const steps = [
      () => result.current.undo(),
      () => result.current.redo(),
      () => result.current.commit(DURATION, true),
      () => result.current.commit({ op: "delete_object", objectId: "plot_1" }, true),
    ];
    for (const run of steps) {
      await act(run);
      await afterDebounce();
      expect(callsOf("render_preview")).toBe(0);
    }
    expect(callsOf("reserve_job_id")).toBe(0);
  });

  it("starts one preview after an export that pre-empted one", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      edit_document: [stateReply("C:/first"), unchanged("C:/first")],
      reserve_job_id: ["preview-1", "export-2", "preview-3"],
      render_preview: [null, null],
      export_movie: [null],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    await exported(result);
    emit(cancelled("preview-1"));
    expect(result.current.status).toBe("Rendering export");
    expect(callsOf("render_preview")).toBe(1);
    emit({ event: "done", jobId: "export-2", path: "C:/first/.mathimator/exports/scene.mp4", current: true });
    await waitFor(() => expect(callsOf("render_preview")).toBe(2));
    expect(invokeMock).toHaveBeenLastCalledWith("render_preview", { jobId: "preview-3" });
    await afterDebounce();
    expect(callsOf("render_preview")).toBe(2);
  });

  it("patches the index from the current preview, and not once an undo overtook it", async () => {
    script({
      get_state: [indexedReply(null)],
      worker_status: [null],
      edit_document: [indexedReply(null)],
      reserve_job_id: ["preview-1"],
      render_preview: [null],
      undo_document: [indexedReply("C:/first/old.mp4")],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    emit({ event: "beat_ready", jobId: "preview-1", beatIndex: 0, path: "C:/first/new.mp4" });
    expect(result.current.state?.index?.beats[0]).toMatchObject({ partial: "C:/first/new.mp4", fresh: true });
    expect(result.current.status).toBe("Rendering preview — beat 1");
    await act(() => result.current.undo());
    emit({ event: "beat_ready", jobId: "preview-1", beatIndex: 0, path: "C:/first/late.mp4" });
    expect(result.current.state?.index?.beats[0]).toMatchObject({ partial: "C:/first/old.mp4", fresh: false });
  });

  it.each([
    ["ignores", true, { partial: null, fresh: false }],
    ["still patches", false, { partial: "C:/first/new.mp4", fresh: true }],
  ] as const)("%s the preview's beat_ready after an edit with changed: %s", async (_, changed, beat) => {
    script({
      get_state: [indexedReply(null)],
      worker_status: [null],
      edit_document: [indexedReply(null), { ...indexedReply(null), changed }],
      reserve_job_id: ["preview-1"],
      render_preview: [null],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    await act(() => result.current.commit({ op: "set_project_name", name: "Renamed" }, false));
    emit({ event: "beat_ready", jobId: "preview-1", beatIndex: 0, path: "C:/first/new.mp4" });
    expect(result.current.state?.index?.beats[0]).toMatchObject(beat);
  });
});

describe("useEditor export", () => {
  it("sends no export when Cancel export comes before the send", async () => {
    script({ get_state: [stateReply("C:/first")], worker_status: [null], reserve_job_id: ["export-1"] });
    const result = await started();
    act(() => {
      result.current.onStartExport(GIF, false);
      result.current.onCancelExport();
    });
    expect(result.current.status).toBe("Rendering export");
    await waitFor(() => expect(result.current.status).toBe("Cancelled"));
    expect(result.current.exporting).toBe(false);
    expect(commands()).toEqual(["get_state", "worker_status", "reserve_job_id"]);
  });

  it("ends the export without sending it when the profile is refused", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      reserve_job_id: ["export-1"],
      edit_document: [refusal("invalid_edit", "That export profile is not supported.")],
    });
    const result = await started();
    act(() => result.current.onStartExport(GIF, false));
    await waitFor(() => expect(result.current.error).toBe("That export profile is not supported."));
    expect(result.current.exporting).toBe(false);
    expect(result.current.status).toBe("Ready");
    expect(callsOf("export_movie")).toBe(0);
  });

  it("ends the export with a banner when export_movie fails", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [new Rejection({ code: "io", message: "The worker's input is closed." })],
    });
    const result = await started();
    act(() => result.current.onStartExport(PROFILE, false));
    await waitFor(() => expect(result.current.error).toBe("The worker's input is closed."));
    expect(result.current.exporting).toBe(false);
    expect(result.current.status).toBe("Ready");
  });

  it("does not carry a Cancel pressed during a refused profile edit into the next export (F047)", async () => {
    const edit = deferred();
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      reserve_job_id: ["export-1", "export-2"],
      edit_document: [edit.promise, unchanged("C:/first")],
      export_movie: [null],
    });
    const result = await started();
    act(() => result.current.onStartExport(GIF, false));
    await waitFor(() => expect(callsOf("edit_document")).toBe(1));
    act(() => result.current.onCancelExport());
    edit.resolve(refusal("invalid_edit", "That export profile is not supported."));
    await waitFor(() => expect(result.current.exporting).toBe(false));
    expect(result.current.status).toBe("Ready");
    await exported(result);
    expect(invokeMock).toHaveBeenLastCalledWith("export_movie", { jobId: "export-2", force: false });
    expect(callsOf("cancel_render")).toBe(0);
    expect(result.current.exporting).toBe(true);
    expect(result.current.status).toBe("Rendering export");
  });

  it("saves the profile before every export, and Re-export sends force (C10, D56)", async () => {
    const redoable = stateReply("C:/first");
    redoable.derived = { ...redoable.derived, canRedo: true };
    script({
      get_state: [redoable],
      worker_status: [null],
      reserve_job_id: ["export-1", "export-2"],
      edit_document: [{ ...redoable, changed: false }, stateReply("C:/first", "Sine", GIF)],
      export_movie: [null, null],
    });
    const result = await started();
    act(() => result.current.onStartExport(PROFILE, true));
    await waitFor(() => expect(callsOf("export_movie")).toBe(1));
    expect(invokeMock).toHaveBeenCalledWith("edit_document", { edit: { op: "set_export_profile", ...PROFILE } });
    expect(invokeMock).toHaveBeenLastCalledWith("export_movie", { jobId: "export-1", force: true });
    expect(result.current.state?.derived.canRedo).toBe(true);
    emit({ event: "done", jobId: "export-1", path: "C:/first/.mathimator/exports/a/scene.mp4", current: true });
    act(() => result.current.onStartExport(GIF, false));
    await waitFor(() => expect(callsOf("export_movie")).toBe(2));
    expect(invokeMock).toHaveBeenCalledWith("edit_document", { edit: { op: "set_export_profile", ...GIF } });
    expect(invokeMock).toHaveBeenLastCalledWith("export_movie", { jobId: "export-2", force: false });
    expect(result.current.state?.document.exportProfile).toEqual(GIF);
    await afterDebounce();
    expect(callsOf("render_preview")).toBe(0);
  });

  it.each([
    ["reserve_job_id", ["reserve_job_id", "get_state"]],
    ["edit_document", ["reserve_job_id", "edit_document", "get_state"]],
  ] as const)("stops the Export task when the worker exits during its %s (C8.6)", async (held, sent) => {
    const pending = deferred();
    const replies = { reserve_job_id: "export-1", edit_document: unchanged("C:/first") };
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/first")],
      worker_status: [null],
      reserve_job_id: [held === "reserve_job_id" ? pending.promise : replies.reserve_job_id],
      edit_document: [held === "edit_document" ? pending.promise : replies.edit_document],
      export_movie: [null],
    });
    const result = await started();
    act(() => result.current.onStartExport(PROFILE, false));
    await waitFor(() => expect(callsOf(held)).toBe(1));
    emit(EXIT);
    pending.resolve(replies[held]);
    await waitFor(() => expect(result.current.note).toBe(RESTART_NOTE));
    expect(commands().slice(2)).toEqual(sent);
    expect(result.current.exporting).toBe(false);
    expect(result.current.status).toBe("Ready");
  });

  it("cancels a sent export and says Cancelled", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [null],
      cancel_render: [null],
    });
    const result = await started();
    await exported(result);
    act(() => result.current.onCancelExport());
    await waitFor(() => expect(callsOf("cancel_render")).toBe(1));
    expect(invokeMock).toHaveBeenLastCalledWith("cancel_render", { jobId: "export-1" });
    expect(result.current.status).toBe("Rendering export");
    emit(cancelled("export-1"));
    expect(result.current.status).toBe("Cancelled");
    expect(result.current.exporting).toBe(false);
  });

  it("says an export is already running on a second Export click", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [null],
    });
    const result = await started();
    await exported(result);
    act(() => result.current.onExport());
    expect(result.current.note).toBe("An export is already running.");
    expect(result.current.presetOpen).toBe(false);
    emit({ event: "started", jobId: "export-1" });
    expect(result.current.note).toBe("An export is already running.");
  });
});

describe("useEditor worker exit", () => {
  it("ends the export, resyncs once for two exits and shows the restart note (C8.6)", async () => {
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/first", "Reloaded")],
      worker_status: [null],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [null],
    });
    const result = await started();
    act(() => {
      result.current.setSelection({ kind: "beat", id: "beat_9" });
      result.current.onDraftDuration("beat_1", 3);
    });
    await exported(result);
    emit(EXIT);
    emit(EXIT);
    expect(result.current.exporting).toBe(false);
    await waitFor(() => expect(result.current.state?.document.name).toBe("Reloaded"));
    expect(result.current.status).toBe("Ready");
    expect(result.current.note).toBe(RESTART_NOTE);
    expect(result.current.drafts).toEqual({});
    expect(result.current.selection).toEqual({ kind: "none" });
    await afterDebounce();
    expect(callsOf("get_state")).toBe(2);
    expect(callsOf("render_preview")).toBe(0);
  });

  it("runs the Open reset and shows no note when the restarted worker is on another folder", async () => {
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/fallback")],
      worker_status: [null],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [null],
    });
    const result = await started();
    await exported(result);
    emit({ event: "done", jobId: "export-1", path: "C:/first/.mathimator/exports/a/scene.mp4", current: false });
    act(() => {
      result.current.setSelection({ kind: "object", id: "plot_1" });
      result.current.setPlayhead(1);
      result.current.onDraftDuration("beat_1", 3);
    });
    emit(EXIT);
    await waitFor(() => expect(result.current.state?.projectPath).toBe("C:/fallback"));
    expect(result.current.selection).toEqual({ kind: "none" });
    expect(result.current.drafts).toEqual({});
    expect(result.current.playhead).toBe(0);
    expect(result.current.exportPath).toBeNull();
    expect(result.current.note).toBeNull();
    expect(result.current.status).toBe("Ready");
  });

  it("resyncs again when the worker exits while the resync runs (C8.6)", async () => {
    const resync = deferred();
    script({
      get_state: [stateReply("C:/first"), resync.promise, stateReply("C:/first", "Reloaded")],
      worker_status: [null],
    });
    const result = await started();
    act(() => result.current.onDraftDuration("beat_1", 3));
    emit(EXIT);
    await waitFor(() => expect(callsOf("get_state")).toBe(2));
    emit(EXIT);
    resync.reject(EXITED);
    await waitFor(() => expect(result.current.state?.document.name).toBe("Reloaded"));
    expect(callsOf("get_state")).toBe(3);
    expect(result.current.note).toBe(RESTART_NOTE);
    expect(result.current.drafts).toEqual({});
  });
});

describe("useEditor field errors", () => {
  const edit = { op: "set_expression", objectId: "plot_1", expression: "x^" } as const;

  it("shows a field error for a field code", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      edit_document: [refusal("unsupported_expression", "That expression is not supported.")],
    });
    const result = await started();
    await act(() => result.current.commit(edit, true));
    expect(result.current.error).toBe("That expression is not supported.");
    expect(result.current.fieldError).toBe("That expression is not supported.");
  });

  it("shows no field error for another code", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      edit_document: [refusal("project_changed", "project.json changed on disk.")],
    });
    const result = await started();
    await act(() => result.current.commit(edit, true));
    expect(result.current.error).toBe("project.json changed on disk.");
    expect(result.current.fieldError).toBeNull();
  });
});
