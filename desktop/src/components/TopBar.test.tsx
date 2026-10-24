// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";

afterEach(cleanup);

const PROJECT_ACTIONS = ["new-button", "open-button", "save-as-button", "sample-button"];

function renderTopBar(exporting: boolean, handlers: { onNew?: () => void; onSaveAs?: () => void } = {}): void {
  const ignore = (): void => undefined;
  render(
    <TopBar
      name="Sine"
      projectPath="C:/Users/Ana/Mathimator/Sine"
      ready
      canUndo
      canRedo={false}
      exporting={exporting}
      onRename={() => Promise.resolve()}
      onNew={handlers.onNew ?? ignore}
      onOpen={ignore}
      onSaveAs={handlers.onSaveAs ?? ignore}
      onAdd={ignore}
      onUndo={ignore}
      onRedo={ignore}
      onExport={ignore}
      onViewCode={ignore}
      onNotices={ignore}
      onOpenSample={ignore}
      onClearCache={ignore}
    />,
  );
}

function button(testId: string): HTMLButtonElement {
  return screen.getByTestId<HTMLButtonElement>(testId);
}

describe("TopBar", () => {
  it("disables New, Open, Save As and Sample while an export runs and keeps Export clickable (D22, C7.6)", () => {
    renderTopBar(true);
    for (const testId of PROJECT_ACTIONS) {
      expect(button(testId).disabled).toBe(true);
    }
    expect(button("export-button").disabled).toBe(false);
    expect(button("export-button").textContent).toBe("Rendering export");
    expect(button("undo-button").disabled).toBe(false);
  });

  it("enables the project actions and labels the button Export otherwise", () => {
    renderTopBar(false);
    for (const testId of PROJECT_ACTIONS) {
      expect(button(testId).disabled).toBe(false);
    }
    expect(button("export-button").textContent).toBe("Export");
  });

  it("shows the project folder and sends New and Save As (D29)", () => {
    const onNew = vi.fn();
    const onSaveAs = vi.fn();
    renderTopBar(false, { onNew, onSaveAs });
    expect(screen.getByTestId("project-path").textContent).toBe("C:/Users/Ana/Mathimator/Sine");
    fireEvent.click(button("new-button"));
    fireEvent.click(button("save-as-button"));
    expect(onNew).toHaveBeenCalledOnce();
    expect(onSaveAs).toHaveBeenCalledOnce();
  });
});
