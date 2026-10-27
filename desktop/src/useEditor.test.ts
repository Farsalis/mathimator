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

const READY = { worker: "ready", reason: null, tex: { state: "ok", message: null }, notices: [] };
const UNAVAILABLE_REASON = "The installed runtime is incomplete (python). Reinstall Mathimator.";
const TIMEOUT = { code: "timeout", message: "No reply within 30 s." };
const TIMEOUT_TEXT = "The worker did not answer in time.";
const CHANGED_TEXT = "project.json was changed outside Mathimator.";
const RESTART_NOTE = "The worker restarted. The document was reloaded from disk and undo history was cleared.";
const PROFILE: ExportProfile = { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false };
const GIF: ExportProfile = { format: "gif", width: 854, height: 480, fps: 15, transparent: false };
const DURATION = { op: "set_beat_duration", beatId: "beat_1", duration: 3 } as const;
const RENAME = { op: "set_project_name", name: "Renamed" } as const;
const EXPRESSION = { op: "set_expression", objectId: "plot_1", expression: "x^" } as const;
const EXIT = { event: "worker_exited", jobId: null, message: "The worker stopped (exit code 1).", exitCode: 1 };
const EXITED = { code: "worker_exited", message: "The worker stopped (exit code 1)." };
const EXPORTED = "C:/first/.mathimator/exports/a/scene.mp4";
const OLDER_EXPORT = "This file is from an older export.";
const CLIP_REMOVED = "manim used an incomplete clip 2.mp4; it was removed, so render again";

/** A scripted reply that rejects with `reason`. */
class Rejection {
  constructor(readonly reason: unknown) {}
}

/**
 * Scripts the shell: each invoke takes the next reply listed for its command, and a Rejection rejects. The queue sends
 * one request at a time, so the replies of one command are taken in the order the UI sends them. `app_status` says
 * the worker is ready unless the script lists other replies.
 */
function script(replies: Record<string, unknown[]>): void {
  const queued: Record<string, unknown[]> = { app_status: [READY], ...replies };
  invokeMock.mockImplementation((command) => {
    const reply = queued[command]?.shift();
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

/** The commands the UI sent, without the dev timing marker. */
function commands(): string[] {
  return invokeMock.mock.calls.map(([command]) => command).filter((command) => command !== "note_shell_ready");
}

function callsOf(command: string): number {
  return commands().filter((name) => name === command).length;
}

function argsOf(command: string): unknown[] {
  return invokeMock.mock.calls.filter(([name]) => name === command).map(([, args]) => args);
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
    derived: { starts: [0], total: 2, dirty: [true], canUndo: false, canRedo: false, onScreen: [[], ["plot_1"]] },
    index: null,
    source: "",
  };
}

/** The reply to an edit that changed nothing, such as the Export task's unchanged profile. */
function unchanged(projectPath: string) {
  return { ...stateReply(projectPath), changed: false };
}

/** A state reply whose preview index holds beat 0 with `partial`, not fresh. */
function indexedReply(partial: string | null) {
  const beat = { id: "beat_1", index: 0, derivedStart: 0, duration: 2, partial, lastGood: null, fresh: false };
  return { ...stateReply("C:/first"), index: { output: null, beats: [beat] } };
}

function refusal(code: string, message: string, field?: string) {
  return { event: "rejected", jobId: "rpc-2", code, message, field };
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
    reserve_job_id: ["export-1"],
    edit_document: [unchanged(first), new Rejection(TIMEOUT)],
    export_movie: [null],
    export_code_dialog: [{ path: `${first}/scene.py` }],
  });
  const result = await started();
  await exported(result);
  emit({ event: "done", jobId: "export-1", path: `${first}/scene.mp4`, current: true });
  act(() => {
    result.current.selectObject("plot_1");
    result.current.setPlayhead(1.5);
    result.current.setPlaying(true);
    result.current.onViewCode();
    result.current.onDraftDuration("beat_1", 3);
  });
  await act(() => result.current.onExportCode());
  await act(() => result.current.commit({ op: "set_project_name", name: "Late" }, false));
  await waitFor(() => expect(result.current.state?.document.name).toBe("Resynced"));
  expect(commands()).toEqual([
    "app_status",
    "get_state",
    "reserve_job_id",
    "edit_document",
    "export_movie",
    "export_code_dialog",
    "edit_document",
    "get_state",
  ]);
  return result.current;
}

/** Starts the editor, then queues two renames; the shell refuses the first with project_changed. */
async function refusedRenames(): Promise<{ current: ReturnType<typeof useEditor> }> {
  const result = await started();
  act(() => {
    void result.current.commit({ op: "set_project_name", name: "First" }, false);
    void result.current.commit({ op: "set_project_name", name: "Second" }, false);
  });
  await waitFor(() => expect(result.current.changedPrompt).toBe(CHANGED_TEXT));
  return result;
}

afterEach(() => {
  cleanup();
  invokeMock.mockReset();
  listenMock.mockClear();
});

describe("useEditor startup", () => {
  it("loads the state once app_status says the worker is ready", async () => {
    script({ get_state: [stateReply("C:/first")] });
    const { result } = renderHook(() => useEditor());
    expect(result.current.status).toBe("Starting");
    expect(result.current.ready).toBe(false);
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.status).toBe("Ready");
    expect(commands()).toEqual(["app_status", "get_state"]);
    expect(invokeMock).toHaveBeenCalledWith("note_shell_ready");
  });

  it("recovers through the resync when the startup get_state times out", async () => {
    script({ get_state: [new Rejection(TIMEOUT), stateReply("C:/first")] });
    const { result } = renderHook(() => useEditor());
    await waitFor(() => expect(result.current.state?.projectPath).toBe("C:/first"));
    expect(commands()).toEqual(["app_status", "get_state", "get_state"]);
    expect(result.current.ready).toBe(true);
    expect(result.current.status).toBe("Ready");
    expect(result.current.error?.message).toBe(TIMEOUT_TEXT);
  });
});

describe("useEditor worker status", () => {
  const unavailable = { ...READY, worker: "unavailable", reason: UNAVAILABLE_REASON };

  it("shows Worker unavailable with the reason, and Retry restarts the worker and loads it", async () => {
    script({ app_status: [unavailable], restart_worker: [READY], get_state: [stateReply("C:/first")] });
    const { result } = renderHook(() => useEditor());
    await waitFor(() => expect(result.current.unavailable).toBe(UNAVAILABLE_REASON));
    expect(result.current.status).toBe("Worker unavailable");
    expect(result.current.ready).toBe(false);
    await act(() => result.current.onRetry());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.unavailable).toBeNull();
    expect(result.current.status).toBe("Ready");
    expect(result.current.note).toBeNull();
    expect(commands()).toEqual(["app_status", "restart_worker", "get_state"]);
  });

  it("keeps the view with the new reason when Retry fails again", async () => {
    const again = { ...unavailable, reason: "The worker did not start within 30 s." };
    script({ app_status: [unavailable], restart_worker: [again] });
    const { result } = renderHook(() => useEditor());
    await waitFor(() => expect(result.current.unavailable).toBe(UNAVAILABLE_REASON));
    await act(() => result.current.onRetry());
    expect(result.current.unavailable).toBe("The worker did not start within 30 s.");
    expect(callsOf("get_state")).toBe(0);
  });

  it("shows the unavailable view when a command fails with worker_unavailable", async () => {
    const reason = "The previous worker did not stop. Choose Retry to try again, or restart Mathimator.";
    script({
      get_state: [stateReply("C:/first")],
      undo_document: [new Rejection({ code: "worker_unavailable", message: reason })],
    });
    const result = await started();
    await act(() => result.current.undo());
    expect(result.current.unavailable).toBe(reason);
    expect(result.current.status).toBe("Worker unavailable");
    expect(result.current.ready).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("shows each notice once and dismisses it with dismiss_notice", async () => {
    const notice = { id: 4, message: "Could not reopen C:/old: gone. A new project was started." };
    script({ app_status: [{ ...READY, notices: [notice] }], get_state: [stateReply("C:/first")] });
    const result = await started();
    expect(result.current.notices).toEqual([notice]);
    emit({ event: "notice", jobId: null, ...notice });
    emit({ event: "notice", jobId: null, id: 5, message: "Could not reopen C:/first after the worker restarted." });
    expect(result.current.notices.map((item) => item.id)).toEqual([4, 5]);
    act(() => result.current.onDismissNotice(4));
    expect(result.current.notices.map((item) => item.id)).toEqual([5]);
    expect(invokeMock).toHaveBeenCalledWith("dismiss_notice", { id: 4 });
    emit({ event: "notice", jobId: null, ...notice });
    expect(result.current.notices.map((item) => item.id)).toEqual([5]);
  });

  it("warns while the TeX check has failed", async () => {
    const failed = "Formulas and axis labels cannot render on this PC: ! I can't find file. Reinstall Mathimator.";
    script({ app_status: [{ ...READY, tex: { state: "checking", message: null } }], get_state: [stateReply("C:/a")] });
    const result = await started();
    expect(result.current.texWarning).toBeNull();
    emit({ event: "tex_status", jobId: null, state: "failed", message: failed });
    expect(result.current.texWarning).toBe(failed);
    emit({ event: "tex_status", jobId: null, state: "ok", message: null });
    expect(result.current.texWarning).toBeNull();
  });

  it("keeps a finished TeX check when the app_status reply that still says checking comes later", async () => {
    const failed = "Formulas and axis labels cannot render on this PC: ! I can't find file. Reinstall Mathimator.";
    const status = deferred();
    script({ app_status: [status.promise], get_state: [stateReply("C:/a")] });
    const { result } = renderHook(() => useEditor());
    await waitFor(() => expect(callsOf("app_status")).toBe(1));
    emit({ event: "tex_status", jobId: null, state: "failed", message: failed });
    status.resolve({ ...READY, tex: { state: "checking", message: null } });
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.texWarning).toBe(failed);
  });
});

describe("useEditor timeout resync", () => {
  it("runs the Open reset when the resync shows another folder", async () => {
    const editor = await editAfterTimeout("C:/first", "C:/second");
    expect(editor.state?.projectPath).toBe("C:/second");
    expect(editor.error?.message).toBe(TIMEOUT_TEXT);
    expect(editor.selection).toEqual({ beatId: null, objectId: null });
    expect(editor.playhead).toBe(0);
    expect(editor.playing).toBe(false);
    expect(editor.panel).toBe("inspector");
    expect(editor.drafts).toEqual({});
    expect(editor.note).toBeNull();
    expect(editor.exportPath).toBeNull();
  });

  it("keeps the view when the resync shows the same folder", async () => {
    const editor = await editAfterTimeout("C:/first", "C:/first");
    expect(editor.state?.projectPath).toBe("C:/first");
    expect(editor.error?.message).toBe(TIMEOUT_TEXT);
    expect(editor.selection).toEqual({ beatId: null, objectId: "plot_1" });
    expect(editor.playhead).toBe(1.5);
    expect(editor.playing).toBe(true);
    expect(editor.panel).toBe("code");
    expect(editor.drafts).toEqual({ beat_1: 3 });
    expect(editor.note).toBe("Code exported to C:/first/scene.py");
    expect(editor.exportPath).toBe("C:/first/scene.mp4");
  });
});

describe("useEditor previews", () => {
  it("ends a preview whose render_preview invoke times out", async () => {
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/first")],
      edit_document: [stateReply("C:/first")],
      reserve_job_id: ["preview-1"],
      render_preview: [new Rejection(TIMEOUT)],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("get_state")).toBe(2));
    await act(() => Promise.resolve());
    expect(commands()).toEqual([
      "app_status",
      "get_state",
      "edit_document",
      "reserve_job_id",
      "render_preview",
      "get_state",
    ]);
    expect(result.current.status).toBe("Ready");
    expect(result.current.error?.message).toBe(TIMEOUT_TEXT);
  });

  it.each(["onOpen", "onOpenSample", "onNew", "onClearCache"] as const)(
    "starts one preview after %s",
    async (action) => {
      script({
        get_state: [stateReply("C:/first")],
        open_project_dialog: [stateReply("C:/second")],
        open_sample: [stateReply("C:/sample")],
        new_project: [stateReply("C:/new")],
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
    },
  );

  it("starts a preview with Render again after a preview used an incomplete clip, and clears the banner", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [stateReply("C:/first")],
      reserve_job_id: ["preview-1", "preview-2"],
      render_preview: [null, null],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    emit({ event: "error", jobId: "preview-1", code: "render_failed", message: CLIP_REMOVED });
    expect(result.current.error).toMatchObject({ job: "preview", code: "render_failed", message: CLIP_REMOVED });
    expect(result.current.status).toBe("Ready");
    act(() => result.current.onRenderAgain());
    expect(result.current.error).toBeNull();
    await waitFor(() => expect(callsOf("render_preview")).toBe(2));
    expect(invokeMock).toHaveBeenLastCalledWith("render_preview", { jobId: "preview-2" });
    expect(result.current.status).toBe("Rendering preview");
  });

  it("starts none after undo, redo, a no-op edit or an edit that leaves no beats", async () => {
    const empty = stateReply("C:/first");
    empty.document.scene = { ...empty.document.scene, objects: [], beats: [] };
    script({
      get_state: [stateReply("C:/first")],
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
      edit_document: [indexedReply(null), { ...indexedReply(null), changed }],
      reserve_job_id: ["preview-1"],
      render_preview: [null],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    await act(() => result.current.commit(RENAME, false));
    emit({ event: "beat_ready", jobId: "preview-1", beatIndex: 0, path: "C:/first/new.mp4" });
    expect(result.current.state?.index?.beats[0]).toMatchObject(beat);
  });
});

describe("useEditor projects", () => {
  it.each([
    ["onOpen", "open_project_dialog"],
    ["onOpenSample", "open_sample"],
    ["onNew", "new_project"],
    ["onSaveAs", "save_project_as_dialog"],
  ] as const)("changes nothing when %s is cancelled", async (action, command) => {
    script({ get_state: [stateReply("C:/first")], [command]: [{ cancelled: true }] });
    const result = await started();
    act(() => result.current.selectObject("plot_1"));
    await act(() => result.current[action]());
    await afterDebounce();
    expect(callsOf(command)).toBe(1);
    expect(result.current.state?.projectPath).toBe("C:/first");
    expect(result.current.selection).toEqual({ beatId: null, objectId: "plot_1" });
    expect(result.current.error).toBeNull();
    expect(callsOf("render_preview")).toBe(0);
  });

  it("keeps the view and the export result through Save As, and starts no preview", async () => {
    script({
      get_state: [stateReply("C:/first")],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first"), stateReply("C:/kept", "Renamed")],
      export_movie: [null],
      save_project_as_dialog: [stateReply("C:/kept")],
    });
    const result = await started();
    await exported(result);
    emit({ event: "done", jobId: "export-1", path: EXPORTED, current: false });
    act(() => {
      result.current.selectObject("plot_1");
      result.current.setPlayhead(1);
      result.current.onViewCode();
    });
    await act(() => result.current.onSaveAs());
    // A later state of the new folder keeps the view as well.
    await act(() => result.current.commit(RENAME, false));
    expect(result.current.state?.projectPath).toBe("C:/kept");
    expect(result.current.state?.document.name).toBe("Renamed");
    expect(result.current.selection).toEqual({ beatId: null, objectId: "plot_1" });
    expect(result.current.playhead).toBe(1);
    expect(result.current.panel).toBe("code");
    // The worker copied the exports along, and a temp project's old folder is gone.
    expect(result.current.exportPath).toBe("C:/kept/.mathimator/exports/a/scene.mp4");
    expect(result.current.note).toBe(OLDER_EXPORT);
    await afterDebounce();
    expect(callsOf("render_preview")).toBe(0);
  });

  it.each([
    ["onOpen", "open_project_dialog"],
    ["onOpenSample", "open_sample"],
    ["onNew", "new_project"],
  ] as const)("follows a Save As in the leave warning of %s that opened no other project", async (action, command) => {
    script({ get_state: [stateReply("C:/temp")], [command]: [{ savedAs: stateReply("C:/kept") }] });
    const result = await started();
    act(() => {
      result.current.selectObject("plot_1");
      result.current.setPlayhead(1);
      result.current.onViewCode();
    });
    await act(() => result.current[action]());
    expect(result.current.state?.projectPath).toBe("C:/kept");
    expect(result.current.selection).toEqual({ beatId: null, objectId: "plot_1" });
    expect(result.current.playhead).toBe(1);
    expect(result.current.panel).toBe("code");
    expect(result.current.error).toBeNull();
    await afterDebounce();
    expect(callsOf("render_preview")).toBe(0);
  });

  it("ignores a beat_ready of the preview that Save As overtook", async () => {
    script({
      get_state: [indexedReply(null)],
      edit_document: [indexedReply(null)],
      reserve_job_id: ["preview-1"],
      render_preview: [null],
      save_project_as_dialog: [{ ...indexedReply(null), projectPath: "C:/kept" }],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    await act(() => result.current.onSaveAs());
    emit({ event: "beat_ready", jobId: "preview-1", beatIndex: 0, path: "C:/first/new.mp4" });
    expect(result.current.state?.index?.beats[0]).toMatchObject({ partial: null, fresh: false });
  });

  it("keeps the export path and note through Clear cache, which keeps the exports", async () => {
    script({
      get_state: [stateReply("C:/first")],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [null],
      clear_cache: [stateReply("C:/first")],
    });
    const result = await started();
    await exported(result);
    emit({ event: "done", jobId: "export-1", path: EXPORTED, current: false });
    await act(() => result.current.onClearCache());
    expect(callsOf("clear_cache")).toBe(1);
    expect(result.current.exportPath).toBe(EXPORTED);
    expect(result.current.note).toBe(OLDER_EXPORT);
  });

  it("updates the code from each applied state without asking the worker", async () => {
    script({
      get_state: [{ ...stateReply("C:/first"), source: "class MainScene: ..." }],
      edit_document: [{ ...stateReply("C:/first"), source: "class Renamed: ..." }],
    });
    const result = await started();
    act(() => result.current.onViewCode());
    expect(result.current.panel).toBe("code");
    expect(result.current.state?.source).toBe("class MainScene: ...");
    await act(() => result.current.commit(RENAME, false));
    expect(result.current.state?.source).toBe("class Renamed: ...");
    expect(commands()).toEqual(["app_status", "get_state", "edit_document"]);
  });

  it("keeps the Notices open through Save As and shows the Inspector after Open", async () => {
    script({
      get_state: [stateReply("C:/temp")],
      save_project_as_dialog: [stateReply("C:/kept")],
      open_project_dialog: [stateReply("C:/other")],
    });
    const result = await started();
    act(() => result.current.onNotices());
    await act(() => result.current.onSaveAs());
    expect(result.current.panel).toBe("notices");
    await act(() => result.current.onOpen());
    expect(result.current.panel).toBe("inspector");
  });
});

describe("useEditor export", () => {
  it("sends no export when Cancel export comes before the send", async () => {
    script({ get_state: [stateReply("C:/first")], reserve_job_id: ["export-1"] });
    const result = await started();
    act(() => {
      result.current.onStartExport(GIF, false);
      result.current.onCancelExport();
    });
    expect(result.current.status).toBe("Rendering export");
    await waitFor(() => expect(result.current.status).toBe("Cancelled"));
    expect(result.current.exporting).toBe(false);
    expect(commands()).toEqual(["app_status", "get_state", "reserve_job_id"]);
  });

  it("ends the export without sending it when the profile is refused", async () => {
    script({
      get_state: [stateReply("C:/first")],
      reserve_job_id: ["export-1"],
      edit_document: [refusal("invalid_edit", "That export profile is not supported.")],
    });
    const result = await started();
    act(() => result.current.onStartExport(GIF, false));
    await waitFor(() => expect(result.current.error?.message).toBe("That export profile is not supported."));
    expect(result.current.exporting).toBe(false);
    expect(result.current.status).toBe("Ready");
    expect(callsOf("export_movie")).toBe(0);
  });

  it("ends the export with a banner when export_movie fails", async () => {
    script({
      get_state: [stateReply("C:/first")],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [new Rejection({ code: "io", message: "The worker's input is closed." })],
    });
    const result = await started();
    act(() => result.current.onStartExport(PROFILE, false));
    await waitFor(() => expect(result.current.error).toEqual({ code: "io", message: "The worker's input is closed." }));
    expect(result.current.exporting).toBe(false);
    expect(result.current.status).toBe("Ready");
  });

  it("does not carry a Cancel pressed during a refused profile edit into the next export", async () => {
    const edit = deferred();
    script({
      get_state: [stateReply("C:/first")],
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

  it("saves the profile before every export, and Re-export sends force", async () => {
    const redoable = stateReply("C:/first");
    redoable.derived = { ...redoable.derived, canRedo: true };
    script({
      get_state: [redoable],
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
  ] as const)("stops the Export task when the worker exits during its %s", async (held, sent) => {
    const pending = deferred();
    const replies = { reserve_job_id: "export-1", edit_document: unchanged("C:/first") };
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/first")],
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

describe("useEditor job ids", () => {
  it.each([
    ["preview", ["edit_document", "reserve_job_id"]],
    ["export", ["reserve_job_id"]],
  ] as const)("sends no %s when reserve_job_id does not answer with a string", async (job, sent) => {
    script({ get_state: [stateReply("C:/first")], edit_document: [stateReply("C:/first")], reserve_job_id: [42] });
    const result = await started();
    if (job === "preview") {
      await act(() => result.current.commit(DURATION, true));
    } else {
      act(() => result.current.onStartExport(PROFILE, false));
    }
    await waitFor(() =>
      expect(result.current.error).toEqual({
        code: null,
        message: "Unexpected reply from the worker: reserve_job_id must be a string.",
      }),
    );
    expect(commands().slice(2)).toEqual(sent);
    expect(result.current.exporting).toBe(false);
    expect(result.current.status).toBe("Ready");
  });
});

describe("useEditor project_changed prompt", () => {
  it("opens one prompt and holds the next edit; Overwrite retries the refused edit, then the next one", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [
        refusal("project_changed", CHANGED_TEXT),
        stateReply("C:/first", "First"),
        stateReply("C:/first", "Second"),
      ],
      overwrite_project: [stateReply("C:/first")],
    });
    const result = await refusedRenames();
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(callsOf("edit_document")).toBe(1);
    expect(result.current.error).toBeNull();
    act(() => result.current.onOverwrite());
    expect(result.current.changedPrompt).toBeNull();
    await waitFor(() => expect(result.current.state?.document.name).toBe("Second"));
    expect(commands().slice(2)).toEqual(["edit_document", "overwrite_project", "edit_document", "edit_document"]);
    expect(argsOf("edit_document")).toEqual([
      { edit: { op: "set_project_name", name: "First" } },
      { edit: { op: "set_project_name", name: "First" } },
      { edit: { op: "set_project_name", name: "Second" } },
    ]);
  });

  it("joins a preview's project_changed refusal to the open prompt and retries it with a new id", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [stateReply("C:/first"), refusal("project_changed", CHANGED_TEXT), stateReply("C:/first")],
      reserve_job_id: ["preview-1", "preview-2"],
      render_preview: [null, null],
      overwrite_project: [stateReply("C:/first")],
    });
    const result = await started();
    await act(() => result.current.commit(DURATION, true));
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    await act(() => result.current.commit(RENAME, false));
    expect(result.current.changedPrompt).toBe(CHANGED_TEXT);
    emit({ event: "rejected", jobId: "preview-1", code: "project_changed", message: CHANGED_TEXT });
    expect(result.current.status).toBe("Ready");
    expect(result.current.error).toBeNull();
    act(() => result.current.onOverwrite());
    await waitFor(() => expect(callsOf("render_preview")).toBe(2));
    expect(result.current.changedPrompt).toBeNull();
    expect(commands().slice(5)).toEqual([
      "edit_document",
      "overwrite_project",
      "edit_document",
      "reserve_job_id",
      "render_preview",
    ]);
    expect(invokeMock).toHaveBeenLastCalledWith("render_preview", { jobId: "preview-2" });
  });

  it.each(["terminal line", "profile edit"] as const)(
    "retries an export refused at its %s as a new Export task with the same profile and force",
    async (refusedAt) => {
      const edit = refusedAt === "profile edit" ? refusal("project_changed", CHANGED_TEXT) : unchanged("C:/first");
      script({
        get_state: [stateReply("C:/first")],
        reserve_job_id: ["export-1", "export-2"],
        edit_document: [edit, unchanged("C:/first")],
        export_movie: [null, null],
        overwrite_project: [stateReply("C:/first")],
      });
      const result = await started();
      act(() => result.current.onStartExport(GIF, true));
      if (refusedAt === "terminal line") {
        await waitFor(() => expect(callsOf("export_movie")).toBe(1));
        emit({ event: "rejected", jobId: "export-1", code: "project_changed", message: CHANGED_TEXT });
      }
      await waitFor(() => expect(result.current.changedPrompt).toBe(CHANGED_TEXT));
      expect(result.current.exporting).toBe(false);
      act(() => result.current.onOverwrite());
      await waitFor(() => expect(argsOf("export_movie").at(-1)).toEqual({ jobId: "export-2", force: true }));
      expect(argsOf("edit_document")).toEqual([
        { edit: { op: "set_export_profile", ...GIF } },
        { edit: { op: "set_export_profile", ...GIF } },
      ]);
      expect(result.current.exporting).toBe(true);
      expect(result.current.status).toBe("Rendering export");
    },
  );

  it("Reload drops the joined refusals, reconciles the view and starts a preview", async () => {
    const reloaded = stateReply("C:/first", "From disk");
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [refusal("project_changed", CHANGED_TEXT)],
      reload_project: [reloaded],
      reserve_job_id: ["preview-1"],
      render_preview: [null],
    });
    const result = await started();
    act(() => {
      result.current.selectObject("formula_9");
      result.current.onDraftDuration("beat_1", 3);
    });
    await act(() => result.current.commit(RENAME, false));
    act(() => result.current.onReload());
    await waitFor(() => expect(callsOf("render_preview")).toBe(1));
    expect(result.current.state?.document.name).toBe("From disk");
    expect(result.current.changedPrompt).toBeNull();
    expect(result.current.selection).toEqual({ beatId: null, objectId: null });
    expect(result.current.drafts).toEqual({});
    expect(callsOf("edit_document")).toBe(1);
  });

  it("Cancel drops the refusal and goes on with the edits that waited", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [refusal("project_changed", CHANGED_TEXT), stateReply("C:/first", "Second")],
    });
    const result = await refusedRenames();
    act(() => result.current.onCancelPrompt());
    await waitFor(() => expect(result.current.state?.document.name).toBe("Second"));
    expect(result.current.changedPrompt).toBeNull();
    expect(callsOf("edit_document")).toBe(2);
    expect(callsOf("overwrite_project") + callsOf("reload_project")).toBe(0);
  });
});

describe("useEditor worker exit", () => {
  it("ends the export, resyncs once for two exits and shows the restart note", async () => {
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/first", "Reloaded")],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [null],
    });
    const result = await started();
    act(() => {
      result.current.selectBeat("beat_9");
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
    expect(result.current.selection).toEqual({ beatId: null, objectId: null });
    await afterDebounce();
    expect(callsOf("get_state")).toBe(2);
    expect(callsOf("render_preview")).toBe(0);
  });

  it("runs the Open reset and shows no note when the restarted worker is on another folder", async () => {
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/fallback")],
      reserve_job_id: ["export-1"],
      edit_document: [unchanged("C:/first")],
      export_movie: [null],
    });
    const result = await started();
    await exported(result);
    emit({ event: "done", jobId: "export-1", path: EXPORTED, current: false });
    act(() => {
      result.current.selectObject("plot_1");
      result.current.setPlayhead(1);
      result.current.onDraftDuration("beat_1", 3);
    });
    emit(EXIT);
    await waitFor(() => expect(result.current.state?.projectPath).toBe("C:/fallback"));
    expect(result.current.selection).toEqual({ beatId: null, objectId: null });
    expect(result.current.drafts).toEqual({});
    expect(result.current.playhead).toBe(0);
    expect(result.current.exportPath).toBeNull();
    expect(result.current.note).toBeNull();
    expect(result.current.status).toBe("Ready");
  });

  it("resyncs again when the worker exits while the resync runs", async () => {
    const resync = deferred();
    script({ get_state: [stateReply("C:/first"), resync.promise, stateReply("C:/first", "Reloaded")] });
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
  const unsupported = "This expression is not supported.";

  it("scopes a field error to the edited object and the refused field", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [refusal("unsupported_expression", unsupported, "expression")],
    });
    const result = await started();
    await act(() => result.current.commit(EXPRESSION, true));
    expect(result.current.error).toEqual({ code: "unsupported_expression", message: unsupported });
    expect(result.current.fieldError).toEqual({
      objectId: "plot_1",
      beatId: null,
      field: "expression",
      op: "set_expression",
      axis: null,
      key: null,
      message: unsupported,
    });
  });

  it("records the op and axis of a refused axes edit, whose field is value for every axes input", async () => {
    const ticks = "value has more than 500 ticks";
    script({ get_state: [stateReply("C:/first")], edit_document: [refusal("limit_exceeded", ticks, "value")] });
    const result = await started();
    await act(() =>
      result.current.commit({ op: "set_axes_range", objectId: "axes_1", axis: "y", value: [0, 1000, 1] }, true),
    );
    expect(result.current.fieldError).toEqual({
      objectId: "axes_1",
      beatId: null,
      field: "value",
      op: "set_axes_range",
      axis: "y",
      key: null,
      message: ticks,
    });
  });

  it.each([
    ["another code", refusal("read_failed", "Could not read project.json: access denied.", "expression")],
    ["a refusal without a field", refusal("unsupported_expression", unsupported)],
  ])("shows no field error for %s", async (_, refused) => {
    script({ get_state: [stateReply("C:/first")], edit_document: [refused] });
    const result = await started();
    await act(() => result.current.commit(EXPRESSION, true));
    expect(result.current.error?.message).toBe(refused.message);
    expect(result.current.fieldError).toBeNull();
  });

  it("clears the field error on undo", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [refusal("unsupported_expression", unsupported, "expression")],
      undo_document: [stateReply("C:/first")],
    });
    const result = await started();
    await act(() => result.current.commit(EXPRESSION, true));
    await act(() => result.current.undo());
    expect(result.current.fieldError).toBeNull();
    expect(result.current.error).toBeNull();
  });
});

/** A state reply whose scene has no objects or beats, as after deleting or undoing the only one. */
function emptyReply(projectPath: string) {
  const reply = stateReply(projectPath);
  return {
    ...reply,
    document: { ...reply.document, scene: { ...reply.document.scene, objects: [], beats: [] } },
    derived: { starts: [], total: 0, dirty: [], canUndo: true, canRedo: false, onScreen: [[]] },
  };
}

/** A state reply whose one beat lasts `duration`. */
function durationReply(duration: number) {
  const reply = stateReply("C:/first");
  const [beat] = reply.document.scene.beats;
  return {
    ...reply,
    document: { ...reply.document, scene: { ...reply.document.scene, beats: [{ ...beat, duration }] } },
    derived: { ...reply.derived, total: duration },
  };
}

describe("useEditor selection", () => {
  const added = { ...stateReply("C:/first"), focus: { beatId: "beat_1", objectId: "plot_1" } };

  it.each([
    ["deleted", () => ({ edit_document: [added, emptyReply("C:/first")] })],
    ["undone", () => ({ edit_document: [added], undo_document: [emptyReply("C:/first")] })],
  ])("selects nothing once the selected beat is %s, so Add does not name it", async (how, replies) => {
    script({ get_state: [emptyReply("C:/first")], ...replies() });
    const result = await started();
    await act(() => result.current.commit({ op: "add_formula", afterBeatId: null }, false));
    expect(result.current.selection).toEqual({ beatId: "beat_1", objectId: "plot_1" });
    await act(() =>
      how === "deleted"
        ? result.current.commit({ op: "delete_object", objectId: "plot_1" }, false)
        : result.current.undo(),
    );
    expect(result.current.selection).toEqual({ beatId: null, objectId: null });
  });

  it("clears the field error when the selection changes", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [refusal("unsupported_expression", "This expression is not supported.", "expression")],
    });
    const result = await started();
    await act(() => result.current.commit(EXPRESSION, true));
    expect(result.current.fieldError).not.toBeNull();
    act(() => result.current.selectBeat("beat_1"));
    expect(result.current.fieldError).toBeNull();
    expect(result.current.selection).toEqual({ beatId: "beat_1", objectId: "plot_1" });
  });
});

/** A state reply whose plot is created, then held by a wait; `focus` names what an edit was about. */
function waitReply(focus?: { beatId: string | null; objectId: string | null }) {
  const reply = stateReply("C:/first");
  const wait = { id: "beat_2", duration: 1, action: "wait", targets: [] };
  return {
    ...reply,
    document: { ...reply.document, scene: { ...reply.document.scene, beats: [...reply.document.scene.beats, wait] } },
    derived: {
      ...reply.derived,
      starts: [0, 2],
      total: 3,
      dirty: [true, true],
      onScreen: [[], ["plot_1"], ["plot_1"]],
    },
    ...(focus === undefined ? {} : { focus }),
  };
}

describe("useEditor two-part selection (spec §6, Selection)", () => {
  it("selects a beat with its first target, keeps the object for a beat without one, and an object keeps the beat", async () => {
    script({ get_state: [waitReply()] });
    const result = await started();
    act(() => result.current.selectBeat("beat_2"));
    expect(result.current.selection).toEqual({ beatId: "beat_2", objectId: null });
    act(() => result.current.selectObject("plot_1"));
    expect(result.current.selection).toEqual({ beatId: "beat_2", objectId: "plot_1" });
    act(() => result.current.selectBeat("beat_1"));
    expect(result.current.selection).toEqual({ beatId: "beat_1", objectId: "plot_1" });
    act(() => result.current.selectBeat("beat_2"));
    expect(result.current.selection).toEqual({ beatId: "beat_2", objectId: "plot_1" });
  });

  it("follows an edit's focus, and keeps the part of the selection it does not name", async () => {
    script({
      get_state: [stateReply("C:/first")],
      edit_document: [waitReply({ beatId: "beat_2", objectId: null }), waitReply({ beatId: null, objectId: "plot_1" })],
    });
    const result = await started();
    act(() => result.current.selectBeat("beat_1"));
    await act(() =>
      result.current.commit({ op: "add_beat", afterBeatId: "beat_1", action: "wait", targets: [] }, true),
    );
    expect(result.current.selection).toEqual({ beatId: "beat_2", objectId: "plot_1" });
    await act(() => result.current.commit({ op: "set_object_name", objectId: "plot_1", name: "Sine" }, false));
    expect(result.current.selection).toEqual({ beatId: "beat_2", objectId: "plot_1" });
  });

  it("drops the selected beat once it is deleted, and keeps the selected object", async () => {
    script({ get_state: [waitReply()], edit_document: [stateReply("C:/first")] });
    const result = await started();
    act(() => result.current.selectBeat("beat_2"));
    act(() => result.current.selectObject("plot_1"));
    await act(() => result.current.commit({ op: "delete_beat", beatId: "beat_2" }, true));
    expect(result.current.selection).toEqual({ beatId: null, objectId: "plot_1" });
  });
});

describe("useEditor beat field errors (spec §6, Refusals in the UI)", () => {
  const outOfRange = "params.factor must be from 0.01 to 100";
  const noEntrance = "scene.objects[0] plot_1 has no entrance beat";

  it("puts a refused beat edit on its beat, with its op and setting", async () => {
    script({ get_state: [stateReply("C:/first")], edit_document: [refusal("invalid_edit", outOfRange, "value")] });
    const result = await started();
    await act(() => result.current.commit({ op: "set_beat_param", beatId: "beat_1", key: "factor", value: 200 }, true));
    expect(result.current.fieldError).toEqual({
      objectId: null,
      beatId: "beat_1",
      field: "value",
      op: "set_beat_param",
      axis: null,
      key: "factor",
      message: outOfRange,
    });
    expect(result.current.error?.message).toBe(outOfRange);
  });

  it("puts a beat edit refused by a document rule, which names no field, on its beat", async () => {
    script({ get_state: [stateReply("C:/first")], edit_document: [refusal("invalid_edit", noEntrance)] });
    const result = await started();
    await act(() => result.current.commit({ op: "delete_beat", beatId: "beat_1" }, true));
    expect(result.current.fieldError).toMatchObject({ beatId: "beat_1", field: null, op: "delete_beat" });
  });

  it("shows a refused + Beat in the banner only", async () => {
    script({ get_state: [stateReply("C:/first")], edit_document: [refusal("invalid_edit", noEntrance, "targets")] });
    const result = await started();
    await act(() => result.current.commit({ op: "add_beat", afterBeatId: null, action: "wait", targets: [] }, true));
    expect(result.current.fieldError).toBeNull();
    expect(result.current.error?.message).toBe(noEntrance);
  });
});

describe("useEditor timeline", () => {
  it("keeps the playhead inside a timeline that got shorter", async () => {
    script({ get_state: [stateReply("C:/first")], edit_document: [durationReply(1)] });
    const result = await started();
    act(() => result.current.setPlayhead(1.5));
    await act(() => result.current.commit({ op: "set_beat_duration", beatId: "beat_1", duration: 1 }, false));
    expect(result.current.total).toBe(1);
    expect(result.current.playhead).toBe(1);
  });

  it("keeps a dragged duration until its edit settles", async () => {
    const reply = deferred();
    script({ get_state: [stateReply("C:/first")], edit_document: [reply.promise] });
    const result = await started();
    act(() => {
      result.current.onDraftDuration("beat_1", 2.9);
      result.current.onCommitDuration("beat_1", 3);
    });
    await waitFor(() => expect(callsOf("edit_document")).toBe(1));
    expect(result.current.drafts).toEqual({ beat_1: 3 });
    expect(result.current.total).toBe(3);
    reply.resolve(durationReply(3));
    await waitFor(() => expect(result.current.drafts).toEqual({}));
    expect(result.current.total).toBe(3);
  });

  it("drops the draft of an interrupted drag without an edit", async () => {
    script({ get_state: [stateReply("C:/first")] });
    const result = await started();
    act(() => {
      result.current.onDraftDuration("beat_1", 4);
      result.current.onCommitDuration("beat_1", 2);
    });
    expect(result.current.drafts).toEqual({});
    expect(callsOf("edit_document")).toBe(0);
  });
});
