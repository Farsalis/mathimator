// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), convertFileSrc: (path: string) => path }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(() => Promise.resolve(() => undefined)) }));

const invokeMock = vi.mocked(invoke);

const READY = { worker: "ready", reason: null, tex: { state: "ok", message: null }, notices: [] };
const REASON = "The installed runtime is incomplete (python). Reinstall Mathimator.";
const CHANGED_TEXT = "project.json was changed outside Mathimator.";
const CLIP_REMOVED = "manim used an incomplete clip 2.mp4; it was removed, so render again";
/** The controls that edit the project or switch it, which the unavailable view disables (C9.5). */
const EDITING = [
  "project-name",
  "new-button",
  "open-button",
  "save-as-button",
  "add-button",
  "add-beat",
  "undo-button",
  "redo-button",
  "export-button",
  "view-code-button",
  "sample-button",
  "clear-cache-button",
];

function stateReply(name = "Sine", source = "class MainScene(Scene):\n    pass\n") {
  return {
    event: "state",
    jobId: "rpc-1",
    projectPath: "C:/Users/Ana/Sine",
    document: {
      name,
      scene: { kind: "scene", objects: [], beats: [], templateInstances: [] },
      exportProfile: { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false },
    },
    derived: { starts: [], total: 0, dirty: [], canUndo: false, canRedo: false, onScreen: [[]] },
    index: null,
    source,
  };
}

/** A state reply whose scene shows one text object in one beat, with a finished preview of it. */
function textReply() {
  const reply = stateReply();
  const movie = "C:/Users/Ana/Sine/.mathimator/cache/MainScene.mp4";
  return {
    ...reply,
    document: {
      ...reply.document,
      scene: {
        ...reply.document.scene,
        objects: [{ id: "text_1", name: "Label", type: "text", params: { text: "Hi", color: "#FFFFFF" } }],
        beats: [{ id: "beat_1", duration: 2, action: "write", targets: ["text_1"] }],
      },
    },
    derived: { starts: [0], total: 2, dirty: [false], canUndo: false, canRedo: false, onScreen: [[], ["text_1"]] },
    index: {
      output: movie,
      beats: [{ id: "beat_1", index: 0, derivedStart: 0, duration: 2, partial: movie, lastGood: null, fresh: true }],
    },
  };
}

/** A caption and a circle, written then created, followed by `beats`; `onScreen` and `focus` as the worker derives them. */
function beatsReply(
  beats: Record<string, unknown>[],
  onScreen: string[][],
  focus?: { beatId: string | null; objectId: string | null },
) {
  const reply = stateReply();
  const durations = beats.map((beat) => (typeof beat.duration === "number" ? beat.duration : 0));
  return {
    ...reply,
    document: {
      ...reply.document,
      scene: {
        ...reply.document.scene,
        objects: [
          { id: "text_1", name: "Caption", type: "text", params: { text: "Hi", color: "#FFFFFF" } },
          { id: "circle_1", name: "Circle", type: "circle", params: CIRCLE_PARAMS },
        ],
        beats,
      },
    },
    derived: {
      starts: durations.map((_, index) => durations.slice(0, index).reduce((sum, value) => sum + value, 0)),
      total: durations.reduce((sum, value) => sum + value, 0),
      dirty: beats.map(() => true),
      canUndo: true,
      canRedo: false,
      onScreen,
    },
    ...(focus === undefined ? {} : { focus }),
  };
}

const CIRCLE_PARAMS = { center: [0, 0], radius: 1, color: "#58C4DD", strokeWidth: 4, fillOpacity: 0 };
const WRITE = { id: "beat_1", duration: 2, action: "write", targets: ["text_1"] };
const CREATE = { id: "beat_2", duration: 2, action: "create", targets: ["circle_1"] };
const BOTH_ON = ["text_1", "circle_1"];

/** Scripts the shell: each invoke takes the next reply listed for its command. */
function script(replies: Record<string, unknown[]>): void {
  invokeMock.mockImplementation((command) => Promise.resolve(replies[command]?.shift()));
}

/** The commands the UI sent, without the dev timing marker. */
function commands(): string[] {
  return invokeMock.mock.calls.map(([command]) => command).filter((command) => command !== "note_shell_ready");
}

/** Sends a line to the app's live `worker-event` listener. */
function emit(payload: unknown): void {
  const handler = vi.mocked(listen).mock.lastCall?.[1];
  if (!handler) {
    throw new Error("The app is not listening to worker-event.");
  }
  act(() => handler({ event: "worker-event", id: 1, payload }));
}

function control(testId: string): HTMLButtonElement | HTMLInputElement {
  return screen.getByTestId<HTMLButtonElement | HTMLInputElement>(testId);
}

async function renderReady(): Promise<void> {
  render(<App />);
  await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("Ready"));
}

function rename(name: string): void {
  const input = control("project-name");
  fireEvent.change(input, { target: { value: name } });
  fireEvent.blur(input);
}

afterEach(() => {
  cleanup();
  invokeMock.mockReset();
  vi.mocked(listen).mockClear();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows Worker unavailable with the reason, disables editing, and Retry restarts the worker (C6, C9.5)", async () => {
    script({
      app_status: [{ ...READY, worker: "unavailable", reason: REASON }],
      restart_worker: [READY],
      get_state: [stateReply()],
    });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("Worker unavailable"));
    expect(screen.getByTestId("unavailable-reason").textContent).toBe(REASON);
    for (const testId of EDITING) {
      expect(control(testId).disabled).toBe(true);
    }
    expect(control("notices-button").disabled).toBe(false);

    fireEvent.click(screen.getByTestId("retry-worker"));
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("Ready"));
    expect(invokeMock).toHaveBeenCalledWith("restart_worker");
    expect(screen.queryByTestId("worker-unavailable")).toBeNull();
    expect(control("open-button").disabled).toBe(false);
  });

  it("shows the shipped third-party notices while the worker is unavailable (C9.5, D75)", async () => {
    const notices = "Third-party notices for Mathimator 0.1.0\n";
    script({ app_status: [{ ...READY, worker: "unavailable", reason: REASON }], third_party_notices: [notices] });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("Worker unavailable"));

    fireEvent.click(control("notices-button"));
    expect((await screen.findByTestId("notices-text")).textContent).toBe(notices);
    expect(commands()).toEqual(["app_status", "third_party_notices"]);
  });

  it("shows a startup notice once when its notice event comes too, and Dismiss calls dismiss_notice (C9.5)", async () => {
    const notice = { id: 7, message: "Could not reopen C:/Users/Ana/Old: gone. A new project was started." };
    script({ app_status: [{ ...READY, notices: [notice] }], get_state: [stateReply()] });
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("Ready"));
    // StrictMode's first mount stops listening before it asks, so only the second mount sends app_status.
    expect(commands().filter((command) => command === "app_status")).toHaveLength(1);
    emit({ event: "notice", jobId: null, ...notice });
    const shown = screen.getAllByTestId("notice");
    expect(shown).toHaveLength(1);
    expect(shown[0]?.textContent).toContain(notice.message);
    fireEvent.click(within(shown[0] as HTMLElement).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryAllByTestId("notice")).toHaveLength(0);
    expect(invokeMock).toHaveBeenCalledWith("dismiss_notice", { id: 7 });
  });

  it("shows the shell's notices for a missing last project and for a close ignored during a dialog (C9.5)", async () => {
    const missing = { id: 1, message: "Could not reopen C:/Users/Ana/Old: it is gone. A new project was started." };
    const ignored = { id: 2, message: "Mathimator stays open while a dialog is open. Close the dialog first." };
    script({
      app_status: [{ ...READY, notices: [missing] }],
      get_state: [stateReply()],
      save_project_as_dialog: [new Promise(() => undefined)],
    });
    await renderReady();
    fireEvent.click(control("save-as-button"));
    await waitFor(() => expect(commands()).toContain("save_project_as_dialog"));
    // The dialog's command still waits, and a notice event is shown at once.
    emit({ event: "notice", jobId: null, ...ignored });
    const shown = screen.getAllByTestId("notice").map((notice) => within(notice).getByRole("paragraph").textContent);
    expect(shown).toEqual([missing.message, ignored.message]);
  });

  it("starts a preview with the banner's Render again after a preview used an incomplete clip", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
    script({
      app_status: [READY],
      get_state: [textReply()],
      clear_cache: [textReply()],
      reserve_job_id: ["preview-1", "preview-2"],
      render_preview: [null, null],
    });
    await renderReady();
    fireEvent.click(control("clear-cache-button"));
    await waitFor(() => expect(commands()).toContain("render_preview"));
    emit({ event: "error", jobId: "preview-1", code: "render_failed", message: CLIP_REMOVED });
    expect(screen.getByTestId("error").textContent).toBe(CLIP_REMOVED);
    fireEvent.click(screen.getByRole("button", { name: "Render again" }));
    expect(screen.queryByTestId("error-banner")).toBeNull();
    await waitFor(() => expect(invokeMock).toHaveBeenLastCalledWith("render_preview", { jobId: "preview-2" }));
    expect(screen.getByTestId("status").textContent).toBe("Rendering preview");
  });

  it("keeps View code in step with each applied state without asking the worker (D25, C10)", async () => {
    script({
      app_status: [READY],
      get_state: [stateReply()],
      edit_document: [stateReply("Renamed", "class Renamed(Scene):\n    pass\n")],
    });
    await renderReady();
    fireEvent.click(control("view-code-button"));
    expect(screen.getByTestId("code-source").textContent).toBe("class MainScene(Scene):\n    pass\n");
    rename("Renamed");
    await waitFor(() =>
      expect(screen.getByTestId("code-source").textContent).toBe("class Renamed(Scene):\n    pass\n"),
    );
    const commands = invokeMock.mock.calls.map(([command]) => command);
    expect(commands).not.toContain("code");
    expect(commands).not.toContain("view_code");
  });

  it("answers a project_changed refusal with the prompt, not a banner, and Overwrite retries the edit (C9.3)", async () => {
    const changed = { event: "rejected", jobId: "rpc-2", code: "project_changed", message: CHANGED_TEXT };
    script({
      app_status: [READY],
      get_state: [stateReply()],
      edit_document: [changed, stateReply("Renamed")],
      overwrite_project: [stateReply()],
    });
    await renderReady();
    rename("Renamed");
    const prompt = await screen.findByRole("alertdialog");
    expect(within(prompt).getByText(CHANGED_TEXT)).toBeTruthy();
    expect(screen.queryByTestId("error")).toBeNull();
    fireEvent.click(within(prompt).getByRole("button", { name: "Overwrite" }));
    await waitFor(() => expect(control("project-name").value).toBe("Renamed"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(commands()).toEqual(["app_status", "get_state", "edit_document", "overwrite_project", "edit_document"]);
  });

  it("makes the app inert behind the project_changed prompt, and Escape cancels it (C9.3, C13.2 E2)", async () => {
    const changed = { event: "rejected", jobId: "rpc-2", code: "project_changed", message: CHANGED_TEXT };
    script({ app_status: [READY], get_state: [stateReply()], edit_document: [changed] });
    await renderReady();
    rename("Renamed");
    await screen.findByRole("alertdialog");
    for (const testId of ["open-button", "new-button", "add-button", "export-button", "undo-button"]) {
      expect(control(testId).closest("[inert]")).not.toBeNull();
    }
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(control("open-button").closest("[inert]")).toBeNull();
    expect(commands()).toEqual(["app_status", "get_state", "edit_document"]);
  });

  it("shows a failed TeX check under the status line (C9.7)", async () => {
    const message = "Formulas and axis labels cannot render on this PC: ! I can't find file. Reinstall Mathimator.";
    script({ app_status: [{ ...READY, tex: { state: "failed", message } }], get_state: [stateReply()] });
    await renderReady();
    expect(screen.getByTestId("tex-warning").textContent).toBe(message);
  });

  it("sends one edit_document for one use of the colour picker (D08, C10)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
    const recoloured = textReply();
    script({ app_status: [READY], get_state: [textReply()], edit_document: [recoloured] });
    await renderReady();
    fireEvent.click(within(screen.getByTestId("inspector")).getByRole("button", { name: /Label/ }));
    const picker = document.querySelector<HTMLInputElement>('input[type="color"]');
    if (!picker) {
      throw new Error("No colour picker is shown.");
    }
    for (let shade = 0; shade < 10; shade += 1) {
      fireEvent.input(picker, { target: { value: `#00${shade}0ff` } });
    }
    fireEvent.change(picker);
    await waitFor(() => expect(commands()).toContain("edit_document"));
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(invokeMock.mock.calls.filter(([command]) => command === "edit_document")).toEqual([
      ["edit_document", { edit: { op: "set_object_color", objectId: "text_1", color: "#0090FF" } }],
    ]);
  });

  it("adds a beat with + Beat, then changes its kind, moves it, retargets it, eases it and deletes it", async () => {
    const indicate = { id: "beat_3", duration: 1, action: "indicate", targets: ["text_1"] };
    const scale = { ...indicate, action: "scale", params: { factor: 1.5 } };
    const both = { ...scale, targets: BOTH_ON };
    const replies = [
      beatsReply([WRITE, indicate, CREATE], [[], ["text_1"], ["text_1"], BOTH_ON], {
        beatId: "beat_3",
        objectId: "text_1",
      }),
      beatsReply([WRITE, scale, CREATE], [[], ["text_1"], ["text_1"], BOTH_ON], { beatId: "beat_3", objectId: null }),
      beatsReply([WRITE, CREATE, scale], [[], ["text_1"], BOTH_ON, BOTH_ON], { beatId: "beat_3", objectId: null }),
      beatsReply([WRITE, CREATE, both], [[], ["text_1"], BOTH_ON, BOTH_ON], { beatId: "beat_3", objectId: null }),
      beatsReply([WRITE, CREATE, { ...both, easing: "rush_into" }], [[], ["text_1"], BOTH_ON, BOTH_ON], {
        beatId: "beat_3",
        objectId: null,
      }),
      beatsReply([WRITE, CREATE], [[], ["text_1"], BOTH_ON], { beatId: "beat_2", objectId: null }),
    ];
    script({
      app_status: [READY],
      get_state: [beatsReply([WRITE, CREATE], [[], ["text_1"], BOTH_ON])],
      edit_document: replies,
      reserve_job_id: ["preview-1"],
      render_preview: [null],
    });
    await renderReady();
    // The editor is keyed by its beat, so each header is looked up afresh.
    const header = (text: string) => screen.findByText(text);

    fireEvent.click(within(screen.getByTestId("beat-beat_1")).getByRole("button", { name: /Write · Caption/ }));
    fireEvent.click(screen.getByRole("button", { name: "+ Beat" }));
    await header("Indicate · beat_3 · scene.beats[1]");
    expect(screen.getByTestId("beat-beat_3").className).toBe("beat selected");

    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "scale" } });
    await header("Scale · beat_3 · scene.beats[1]");
    fireEvent.click(screen.getByRole("button", { name: "Move later" }));
    await header("Scale · beat_3 · scene.beats[2]");
    fireEvent.click(within(screen.getByTestId("beat-targets")).getByRole("checkbox", { name: "Circle" }));
    await waitFor(() => expect(screen.getByTestId("beat-beat_3").textContent).toContain("Scale · Caption, Circle"));
    fireEvent.change(screen.getByLabelText("Easing"), { target: { value: "rush_into" } });
    await waitFor(() => expect(screen.getByLabelText<HTMLSelectElement>("Easing").value).toBe("rush_into"));
    fireEvent.click(screen.getByRole("button", { name: "Delete beat" }));
    await header("Create · beat_2 · scene.beats[1]");

    expect(invokeMock.mock.calls.filter(([command]) => command === "edit_document")).toEqual(
      [
        { op: "add_beat", afterBeatId: "beat_1", action: "indicate", targets: ["text_1"] },
        { op: "set_beat_action", beatId: "beat_3", action: "scale", targets: ["text_1"] },
        { op: "move_beat", beatId: "beat_3", toIndex: 2 },
        { op: "set_beat_targets", beatId: "beat_3", targets: ["text_1", "circle_1"] },
        { op: "set_beat_easing", beatId: "beat_3", easing: "rush_into" },
        { op: "delete_beat", beatId: "beat_3" },
      ].map((edit) => ["edit_document", { edit }]),
    );
    expect(screen.queryByTestId("beat-beat_3")).toBeNull();
    await waitFor(() => expect(commands()).toContain("render_preview"));
  });

  it("adds a wait at the end with + Beat when nothing is selected", async () => {
    script({
      app_status: [READY],
      get_state: [beatsReply([WRITE, CREATE], [[], ["text_1"], BOTH_ON])],
      edit_document: [new Promise(() => undefined)],
    });
    await renderReady();
    fireEvent.click(control("add-beat"));
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith("edit_document", {
        edit: { op: "add_beat", afterBeatId: null, action: "wait", targets: [] },
      }),
    );
  });

  it("brings the selected object on screen with its natural entrance, after the selected beat", async () => {
    script({
      app_status: [READY],
      get_state: [beatsReply([WRITE, CREATE], [[], ["text_1"], BOTH_ON])],
      edit_document: [new Promise(() => undefined)],
    });
    await renderReady();
    fireEvent.click(within(screen.getByTestId("beat-beat_1")).getByRole("button", { name: /Write · Caption/ }));
    fireEvent.click(within(screen.getByTestId("inspector")).getByRole("button", { name: /Circle/ }));
    fireEvent.click(control("add-beat"));
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith("edit_document", {
        edit: { op: "add_beat", afterBeatId: "beat_1", action: "create", targets: ["circle_1"] },
      }),
    );
  });

  it("shows a refused beat edit under its control as well as in the banner (design §10.7)", async () => {
    const message = "scene.objects[1] circle_1 has no entrance beat";
    script({
      app_status: [READY],
      get_state: [beatsReply([WRITE, CREATE], [[], ["text_1"], BOTH_ON])],
      edit_document: [{ event: "rejected", jobId: "rpc-2", code: "invalid_edit", message }],
    });
    await renderReady();
    fireEvent.click(within(screen.getByTestId("beat-beat_2")).getByRole("button", { name: /Create · Circle/ }));
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "fade_out" } });
    const shown = await screen.findByTestId("beat-error-beat_2");
    expect(shown.textContent).toBe(message);
    expect(shown.previousElementSibling).toBe(screen.getByLabelText("Kind").closest("label"));
    expect(screen.getByTestId("error").textContent).toBe(message);
  });

  it("scrubs the preview without a worker command (D44)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
    script({ app_status: [READY], get_state: [textReply()] });
    await renderReady();
    const sent = commands();
    const playhead = control("playhead");
    for (const time of ["0.5", "1.25", "2"]) {
      fireEvent.input(playhead, { target: { value: time } });
    }
    fireEvent.pointerUp(playhead);
    expect(screen.getByTestId("playhead-time").textContent).toBe("2.0s / 2.0s");
    expect(commands()).toEqual(sent);
  });
});
