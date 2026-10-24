// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExportProfile } from "./exportProfile";
import { useEditor } from "./useEditor";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(() => Promise.resolve(() => undefined)) }));

const invokeMock = vi.mocked(invoke);
const listenMock = vi.mocked(listen);

const TIMEOUT = { code: "timeout", message: "No reply within 30 s." };
const TIMEOUT_TEXT = "The worker did not answer in time.";
const PROFILE: ExportProfile = { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false };

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

function stateReply(projectPath: string, name = "Sine") {
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
      exportProfile: PROFILE,
    },
    derived: { starts: [0], total: 2, dirty: [true], canUndo: false, canRedo: false },
    index: null,
    source: "",
  };
}

function refusal(code: string, message: string) {
  return { event: "rejected", jobId: "rpc-2", code, message };
}

async function started(): Promise<{ current: ReturnType<typeof useEditor> }> {
  const { result } = renderHook(() => useEditor());
  await waitFor(() => expect(result.current.state).not.toBeNull());
  return result;
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
    edit_document: [stateReply(first), new Rejection(TIMEOUT)],
    export_movie: [null],
    export_code_dialog: [{ path: `${first}/scene.py` }],
  });
  const result = await started();
  act(() => result.current.onStartExport(PROFILE));
  await waitFor(() => expect(callsOf("export_movie")).toBe(1));
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
    expect(editor.exportNote).toBeNull();
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
    expect(editor.exportNote).toBe("Code exported to C:/first/scene.py");
    expect(editor.exportPath).toBe("C:/first/scene.mp4");
  });
});

describe("useEditor jobs", () => {
  it("ends a preview whose render_preview invoke times out", async () => {
    script({
      get_state: [stateReply("C:/first"), stateReply("C:/first")],
      worker_status: [null],
      edit_document: [stateReply("C:/first")],
      reserve_job_id: ["preview-1"],
      render_preview: [new Rejection(TIMEOUT)],
    });
    const result = await started();
    await act(() => result.current.commit({ op: "set_beat_duration", beatId: "beat_1", duration: 3 }, true));
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
    expect(result.current.rendering).toBe(false);
    expect(result.current.status).toBe("Ready");
    expect(result.current.error).toBe(TIMEOUT_TEXT);
  });

  it("does not carry a Cancel into the next export after a refused export profile", async () => {
    script({
      get_state: [stateReply("C:/first")],
      worker_status: [null],
      reserve_job_id: ["export-1", "export-2"],
      edit_document: [refusal("invalid_edit", "That export profile is not supported."), stateReply("C:/first")],
      export_movie: [null],
    });
    const result = await started();
    act(() => {
      result.current.onStartExport(PROFILE);
      result.current.onCancelExport();
    });
    await waitFor(() => expect(result.current.error).toBe("That export profile is not supported."));
    expect(result.current.exporting).toBe(false);
    act(() => result.current.onStartExport(PROFILE));
    await waitFor(() => expect(callsOf("export_movie")).toBe(1));
    expect(invokeMock).toHaveBeenLastCalledWith("export_movie", { jobId: "export-2" });
    expect(callsOf("cancel_render")).toBe(0);
    expect(result.current.exporting).toBe(true);
    expect(result.current.status).toBe("Rendering export");
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
