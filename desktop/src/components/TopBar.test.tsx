// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TopBar } from "./TopBar";

afterEach(cleanup);

function renderTopBar(exporting: boolean): void {
  const ignore = (): void => undefined;
  render(
    <TopBar
      name="Sine"
      ready
      canUndo
      canRedo={false}
      exporting={exporting}
      onRename={ignore}
      onOpen={ignore}
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
  it("disables Open and Sample while an export runs and keeps Export clickable (D22)", () => {
    renderTopBar(true);
    expect(button("open-button").disabled).toBe(true);
    expect(button("sample-button").disabled).toBe(true);
    expect(button("export-button").disabled).toBe(false);
    expect(button("export-button").textContent).toBe("Rendering export");
    expect(button("undo-button").disabled).toBe(false);
  });

  it("enables Open and Sample and labels the button Export otherwise", () => {
    renderTopBar(false);
    expect(button("open-button").disabled).toBe(false);
    expect(button("sample-button").disabled).toBe(false);
    expect(button("export-button").textContent).toBe("Export");
  });
});
