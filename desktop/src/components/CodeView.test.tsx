// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CodeView } from "./CodeView";

afterEach(cleanup);

describe("CodeView", () => {
  it("shows the source it is given (D25)", () => {
    render(<CodeView source={"class MainScene(Scene):\n    pass\n"} onClose={vi.fn()} onExport={vi.fn()} />);
    expect(screen.getByTestId("code-source").textContent).toBe("class MainScene(Scene):\n    pass\n");
    expect(screen.getByTestId<HTMLButtonElement>("export-code-button").disabled).toBe(false);
  });

  it("shows why there is no source, and offers no Export code", () => {
    const reason = "eq_1 left the screen when it was transformed into eq_2";
    render(<CodeView source={null} sourceError={reason} onClose={vi.fn()} onExport={vi.fn()} />);
    expect(screen.getByTestId("code-error").textContent).toBe(reason);
    expect(screen.queryByTestId("code-source")).toBeNull();
    expect(screen.getByTestId<HTMLButtonElement>("export-code-button").disabled).toBe(true);
  });
});
