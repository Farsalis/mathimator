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
/** The controls that edit the project or switch it, which the unavailable view disables (C9.5). */
const EDITING = [
  "project-name",
  "new-button",
  "open-button",
  "save-as-button",
  "add-button",
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
    derived: { starts: [], total: 0, dirty: [], canUndo: false, canRedo: false },
    index: null,
    source,
  };
}

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
});
