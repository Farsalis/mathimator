// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExportProfile } from "../exportProfile";
import { ExportPanel } from "./ExportPanel";

afterEach(cleanup);

const PROFILE: ExportProfile = { format: "webm", width: 1280, height: 720, fps: 30, transparent: true };

function button(testId: string): HTMLButtonElement {
  return screen.getByTestId<HTMLButtonElement>(testId);
}

describe("ExportPanel", () => {
  it("starts an export, and a Re-export that skips the export cache", () => {
    const onStart = vi.fn();
    render(<ExportPanel initial={PROFILE} canStart onStart={onStart} onClose={vi.fn()} />);
    fireEvent.click(button("export-start"));
    fireEvent.click(button("re-export"));
    expect(onStart.mock.calls).toEqual([
      [PROFILE, false],
      [PROFILE, true],
    ]);
  });

  it("starts nothing while an export cannot start", () => {
    const onStart = vi.fn();
    render(<ExportPanel initial={PROFILE} canStart={false} onStart={onStart} onClose={vi.fn()} />);
    expect(button("export-start").disabled).toBe(true);
    expect(button("re-export").disabled).toBe(true);
    fireEvent.submit(screen.getByTestId("export-panel"));
    expect(onStart).not.toHaveBeenCalled();
  });
});
