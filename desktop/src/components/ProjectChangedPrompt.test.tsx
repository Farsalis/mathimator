// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectChangedPrompt } from "./ProjectChangedPrompt";

afterEach(cleanup);

const MESSAGE = "project.json was changed outside Mathimator.";

describe("ProjectChangedPrompt", () => {
  it("shows the refusal as a modal prompt with Cancel focused (D59, C13.2 E2)", () => {
    render(<ProjectChangedPrompt message={MESSAGE} onReload={vi.fn()} onOverwrite={vi.fn()} onCancel={vi.fn()} />);
    const dialog = screen.getByRole("alertdialog", { name: "Project changed on disk" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(screen.getByText(MESSAGE)).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByTestId("prompt-cancel"));
  });

  it.each([
    ["prompt-reload", "onReload"],
    ["prompt-overwrite", "onOverwrite"],
    ["prompt-cancel", "onCancel"],
  ] as const)("%s calls %s only", (testId, chosen) => {
    const handlers = { onReload: vi.fn(), onOverwrite: vi.fn(), onCancel: vi.fn() };
    render(<ProjectChangedPrompt message={MESSAGE} {...handlers} />);
    fireEvent.click(screen.getByTestId(testId));
    for (const [name, handler] of Object.entries(handlers)) {
      expect(handler).toHaveBeenCalledTimes(name === chosen ? 1 : 0);
    }
  });

  it("answers Escape with Cancel only, wherever the focus is", () => {
    const handlers = { onReload: vi.fn(), onOverwrite: vi.fn(), onCancel: vi.fn() };
    render(<ProjectChangedPrompt message={MESSAGE} {...handlers} />);
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(handlers.onCancel).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(handlers.onCancel).toHaveBeenCalledTimes(1);
    expect(handlers.onReload).not.toHaveBeenCalled();
    expect(handlers.onOverwrite).not.toHaveBeenCalled();
  });
});
