// @vitest-environment jsdom
import { invoke } from "@tauri-apps/api/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NoticesView } from "./NoticesView";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const invokeMock = vi.mocked(invoke);

const NOTICES = "Third-party notices for Mathimator 0.1.0\n=======\n\n  av 19.0.2: BSD-3-Clause [T3]\n";
const NOT_BUILT =
  "The third-party notices are not built: packaging/bundle has no THIRD_PARTY_NOTICES.txt. Build the bundle, " +
  "then run scripts/generate_notices.py.";

afterEach(() => {
  cleanup();
  invokeMock.mockReset();
});

describe("NoticesView (D75)", () => {
  it("names the licence of each bundled part and where the texts are, above the shipped notices file", async () => {
    invokeMock.mockResolvedValue(NOTICES);
    render(<NoticesView onClose={vi.fn()} />);

    const summary = screen.getByTestId("notices-summary").textContent;
    for (const part of [
      "Mathimator's own source is under the MIT licence",
      "CPython (Python Software Foundation License)",
      "Manim Community Edition (MIT)",
      "PyAV, which ships FFmpeg's libraries (LGPL-3.0-or-later)",
      "x264 and x265 video encoders, under GPL-2.0-or-later",
      "LAME MP3 encoder (LGPL-2.0-or-later)",
      "pycairo (LGPL-2.1 or MPL-1.1)",
      "Typst (Apache-2.0). Its LICENSE and NOTICE are in runtime\\typst in the install folder",
      "TinyTeX, whose TeX programs keep their own licences",
      "The licence texts that ship with these parts, and the written offer of the source code of the GPL and LGPL libraries",
      "runtime\\THIRD_PARTY_NOTICES.txt in the install folder",
      "Microsoft Edge WebView2 Runtime",
    ]) {
      expect(summary).toContain(part);
    }
    const file = await screen.findByTestId("notices-text");
    expect(file.textContent).toBe(NOTICES);
    expect(file.tabIndex).toBe(0);
    expect(invokeMock.mock.calls).toEqual([["third_party_notices"]]);
  });

  it("says it is reading the file until the shell answers", () => {
    invokeMock.mockReturnValue(new Promise(() => undefined));
    render(<NoticesView onClose={vi.fn()} />);

    expect(screen.getByText("Reading the notices…")).toBeTruthy();
    expect(screen.queryByTestId("notices-text")).toBeNull();
    expect(screen.getByTestId("notices-summary")).toBeTruthy();
  });

  it("shows why the file cannot be shown, under the summary", async () => {
    invokeMock.mockRejectedValue({ code: "io", message: NOT_BUILT });
    render(<NoticesView onClose={vi.fn()} />);

    expect((await screen.findByTestId("notices-error")).textContent).toBe(NOT_BUILT);
    expect(screen.queryByTestId("notices-text")).toBeNull();
    expect(screen.getByTestId("notices-summary")).toBeTruthy();
  });

  it("reports a reply that is not text", async () => {
    invokeMock.mockResolvedValue({ text: NOTICES });
    render(<NoticesView onClose={vi.fn()} />);

    expect((await screen.findByTestId("notices-error")).textContent).toBe(
      "Unexpected reply from the worker: third_party_notices must be a string.",
    );
  });

  it("closes with Close", async () => {
    invokeMock.mockResolvedValue(NOTICES);
    const onClose = vi.fn();
    render(<NoticesView onClose={onClose} />);
    await screen.findByTestId("notices-text");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
