// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MAX_TOTAL_SECONDS, MIN_BEAT_SECONDS } from "../format";
import type { Beat, SceneObject } from "../types";
import { Timeline } from "./Timeline";

const BEAT: Beat = { id: "beat_1", duration: 2, action: "create", targets: ["plot_1"] };
const PLOT: SceneObject = { id: "plot_1", name: "Sine", type: "function_plot", params: {} };

let onDraft = vi.fn<(beatId: string, duration: number) => void>();
let onCommit = vi.fn<(beatId: string, duration: number) => void>();
let onAddBeat = vi.fn<() => void>();

/** Renders the timeline of one beat, with `props` in place of the defaults. */
function renderTimeline(props: Partial<ComponentProps<typeof Timeline>> = {}): void {
  cleanup();
  render(
    <Timeline
      beats={[BEAT]}
      objects={[PLOT]}
      durations={[2]}
      starts={[0]}
      dirty={[false]}
      selectedBeatId={null}
      playhead={0}
      total={2}
      disabled={false}
      onSelect={vi.fn()}
      onDraft={onDraft}
      onCommit={onCommit}
      onAddBeat={onAddBeat}
      {...props}
    />,
  );
}

beforeEach(() => {
  // jsdom has no pointer capture.
  HTMLElement.prototype.setPointerCapture = vi.fn();
  onDraft = vi.fn();
  onCommit = vi.fn();
  onAddBeat = vi.fn();
  renderTimeline();
});

afterEach(cleanup);

function handle(): HTMLElement {
  return screen.getByLabelText("Duration of beat_1");
}

describe("Timeline labels", () => {
  it("shows each beat's start, kind and target names, and marks its family", () => {
    const cell = screen.getByTestId("beat-beat_1");
    expect(within(cell).getByRole("button", { name: /Create · Sine/ }).textContent).toBe("0.0sCreate · Sine");
    expect(cell.dataset.family).toBe("entrance");
  });
});

describe("Timeline + Beat (spec §6, + Beat)", () => {
  it("asks for a beat from the toolbar", () => {
    const button = screen.getByRole("button", { name: "+ Beat" });
    expect(button).toBe(screen.getByTestId("add-beat"));
    fireEvent.click(button);
    expect(onAddBeat).toHaveBeenCalledOnce();
  });

  it("is disabled while the worker is not ready", () => {
    renderTimeline({ disabled: true });
    expect(screen.getByTestId<HTMLButtonElement>("add-beat").disabled).toBe(true);
  });

  it("points an empty timeline at Add and + Beat", () => {
    renderTimeline({ beats: [], durations: [], starts: [], dirty: [], total: 0 });
    expect(screen.getByText("No beats yet. Add an object, or use + Beat.")).toBeTruthy();
    expect(screen.getByTestId<HTMLButtonElement>("add-beat").disabled).toBe(false);
  });
});

describe("Timeline duration drag", () => {
  it.each(["mouse", "touch", "pen"])("resizes the beat with a %s, 48 px per second", (pointerType) => {
    fireEvent.pointerDown(handle(), { pointerId: 3, pointerType, clientX: 100 });
    fireEvent.pointerMove(handle(), { pointerId: 3, pointerType, clientX: 148 });
    expect(onDraft).toHaveBeenLastCalledWith("beat_1", 3);
    fireEvent.pointerUp(handle(), { pointerId: 3, pointerType, clientX: 124 });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("beat_1", 2.5);
  });

  it("drafts whole milliseconds, from the shortest beat to the longest timeline (spec §4)", () => {
    fireEvent.pointerDown(handle(), { pointerId: 1, clientX: 500 });
    fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 501 });
    expect(onDraft).toHaveBeenLastCalledWith("beat_1", 2.021);
    fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 500 - 48 * 3 });
    expect(onDraft).toHaveBeenLastCalledWith("beat_1", MIN_BEAT_SECONDS);
    fireEvent.pointerUp(handle(), { pointerId: 1, clientX: 500 + 48 * 200 });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("beat_1", MAX_TOTAL_SECONDS);
  });

  it("ignores every pointer but the one that started the drag", () => {
    fireEvent.pointerDown(handle(), { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(handle(), { pointerId: 2, clientX: 500 });
    fireEvent.pointerUp(handle(), { pointerId: 2, clientX: 500 });
    expect(onDraft).not.toHaveBeenCalled();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("does not let a second pointer that presses the handle take over the drag", () => {
    fireEvent.pointerDown(handle(), { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 148 });
    fireEvent.pointerDown(handle(), { pointerId: 2, clientX: 300 });
    fireEvent.pointerUp(handle(), { pointerId: 2, clientX: 400 });
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.pointerUp(handle(), { pointerId: 1, clientX: 124 });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("beat_1", 2.5);
  });

  it.each([
    ["is cancelled", fireEvent.pointerCancel],
    ["loses the pointer capture", fireEvent.lostPointerCapture],
  ])("goes back to the duration it started from when the drag %s", (_, interrupt) => {
    fireEvent.pointerDown(handle(), { pointerId: 1, pointerType: "touch", clientX: 100 });
    fireEvent.pointerMove(handle(), { pointerId: 1, pointerType: "touch", clientX: 196 });
    expect(onDraft).toHaveBeenLastCalledWith("beat_1", 4);
    interrupt(handle(), { pointerId: 1, pointerType: "touch" });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("beat_1", 2);
    fireEvent.pointerMove(handle(), { pointerId: 1, pointerType: "touch", clientX: 300 });
    fireEvent.pointerUp(handle(), { pointerId: 1, pointerType: "touch", clientX: 300 });
    expect(onDraft).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("does not cancel a drag that ended with a release when the capture is then lost", () => {
    fireEvent.pointerDown(handle(), { pointerId: 1, clientX: 100 });
    fireEvent.pointerUp(handle(), { pointerId: 1, clientX: 124 });
    fireEvent.lostPointerCapture(handle(), { pointerId: 1 });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("beat_1", 2.5);
  });
});
