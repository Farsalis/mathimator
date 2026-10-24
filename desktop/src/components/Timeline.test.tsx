// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Beat } from "../types";
import { Timeline } from "./Timeline";

const BEAT: Beat = { id: "beat_1", duration: 2, action: "create", targets: ["plot_1"] };

let onDraft = vi.fn<(beatId: string, duration: number) => void>();
let onCommit = vi.fn<(beatId: string, duration: number) => void>();

beforeEach(() => {
  // jsdom has no pointer capture.
  HTMLElement.prototype.setPointerCapture = vi.fn();
  onDraft = vi.fn();
  onCommit = vi.fn();
  render(
    <Timeline
      beats={[BEAT]}
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
    />,
  );
});

afterEach(cleanup);

function handle(): HTMLElement {
  return screen.getByLabelText("Duration of beat_1");
}

describe("Timeline duration drag (D48, F110)", () => {
  it.each(["mouse", "touch", "pen"])("resizes the beat with a %s, 48 px per second", (pointerType) => {
    fireEvent.pointerDown(handle(), { pointerId: 3, pointerType, clientX: 100 });
    fireEvent.pointerMove(handle(), { pointerId: 3, pointerType, clientX: 148 });
    expect(onDraft).toHaveBeenLastCalledWith("beat_1", 3);
    fireEvent.pointerUp(handle(), { pointerId: 3, pointerType, clientX: 124 });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("beat_1", 2.5);
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
