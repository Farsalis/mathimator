// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FormulaPreview } from "./FormulaPreview";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), convertFileSrc: (path: string) => path }));

const invokeMock = vi.mocked(invoke);

const SVG = "C:/Users/Ana/Sine/.mathimator/equation/formula-eq_1-1.svg";

afterEach(() => {
  cleanup();
  invokeMock.mockReset();
  vi.unstubAllGlobals();
});

/** Answers each compile with the next reply, a Promise for one that settles later. Cancels resolve at once. */
function script(...replies: unknown[]): void {
  invokeMock.mockImplementation((command) =>
    command === "compile_equation_preview" ? Promise.resolve(replies.shift()) : Promise.resolve(null),
  );
}

function argsOf(command: string): Array<{ slot: string; generation: number; latex?: string }> {
  return invokeMock.mock.calls
    .filter(([name]) => name === command)
    .map(([, args]) => args as { slot: string; generation: number; latex?: string });
}

describe("FormulaPreview (C9.9, F037)", () => {
  it("cancels a compile in flight on unmount, and a remount asks above every earlier cancel", async () => {
    script(new Promise(() => undefined), { status: "unsupported" });
    const first = render(<FormulaPreview slot="eq_1" latex="y = \sin(x)" />);
    await waitFor(() => expect(argsOf("compile_equation_preview")).toHaveLength(1));
    first.unmount();
    const cancels = argsOf("cancel_equation_preview");
    expect(cancels).toEqual([{ slot: "eq_1", generation: argsOf("compile_equation_preview")[0]?.generation }]);

    render(<FormulaPreview slot="eq_1" latex="y = \sin(x)" />);
    await waitFor(() =>
      expect(screen.getByTestId("equation-message").textContent).toBe("No live preview for this formula."),
    );
    const remount = argsOf("compile_equation_preview")[1];
    expect(remount?.slot).toBe("eq_1");
    for (const cancel of cancels) {
      expect(remount?.generation).toBeGreaterThan(cancel.generation);
    }
  });

  it("sends no cancel once the compile has settled", async () => {
    script({ status: "unsupported" });
    const { unmount } = render(<FormulaPreview slot="eq_1" latex="\int x" />);
    await screen.findByTestId("equation-message");
    unmount();
    expect(argsOf("cancel_equation_preview")).toEqual([]);
  });

  it("compiles two previews shown together in their own slots", async () => {
    script({ status: "unsupported" }, { status: "unsupported" });
    render(
      <>
        <FormulaPreview slot="eq_1" latex="y = \sin(x)" />
        <FormulaPreview slot="eq_2" latex="y = \cos(x)" />
      </>,
    );
    await waitFor(() => expect(argsOf("compile_equation_preview")).toHaveLength(2));
    const [one, two] = argsOf("compile_equation_preview");
    expect([one?.slot, two?.slot]).toEqual(["eq_1", "eq_2"]);
    expect(one?.generation).not.toBe(two?.generation);
  });

  it("asks once more when the current request comes back cancelled", async () => {
    script({ status: "cancelled" }, { status: "unsupported" });
    render(<FormulaPreview slot="eq_1" latex="y = \sin(x)" />);
    await screen.findByTestId("equation-message");
    const [first, retry] = argsOf("compile_equation_preview");
    expect(retry?.generation).toBeGreaterThan(first?.generation ?? Infinity);
  });

  it("asks only once more", async () => {
    script({ status: "cancelled" }, { status: "cancelled" }, { status: "unsupported" });
    render(<FormulaPreview slot="eq_1" latex="y = \sin(x)" />);
    await waitFor(() => expect(argsOf("compile_equation_preview")).toHaveLength(2));
    await act(() => new Promise((resolve) => setTimeout(resolve, 200)));
    expect(argsOf("compile_equation_preview")).toHaveLength(2);
    expect(screen.queryByTestId("equation-message")).toBeNull();
  });

  it("keeps the last good SVG and shows the message when a later compile fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("<svg/>"))),
    );
    URL.createObjectURL = vi.fn(() => "blob:glyph");
    URL.revokeObjectURL = vi.fn();
    script({ status: "ok", svgPath: SVG }, { status: "error", message: "typst compile timed out" });
    const { rerender } = render(<FormulaPreview slot="eq_1" latex="y = \sin(x)" />);
    await waitFor(() => expect(screen.getByTestId("equation-svg").getAttribute("src")).toBe("blob:glyph"));
    rerender(<FormulaPreview slot="eq_1" latex="y = \sin(x" />);
    await screen.findByText("typst compile timed out");
    expect(screen.getByTestId("equation-svg").getAttribute("src")).toBe("blob:glyph");
    expect(fetch).toHaveBeenCalledWith(SVG);
  });
});
