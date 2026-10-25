// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import supervisor from "../../../worker/mathimator_worker/supervisor.py?raw";
import type { Failure } from "../workerQueue";
import { ErrorBanner } from "./ErrorBanner";

afterEach(cleanup);

const RENDER_FAILED: Failure = {
  code: "render_failed",
  message: "manim exited with status 1: RuntimeError: boom",
  details: "Traceback (most recent call last):\nRuntimeError: boom",
};

/** The worker's failure for a preview that used a cut-off clip, which it removed. */
const CLIP_REMOVED: Failure = {
  job: "preview",
  code: "render_failed",
  message: "manim used an incomplete clip 2.mp4; it was removed, so render again",
};

/** Renders the banner with its Save As and Render again buttons enabled or disabled. */
function renderBanner(failure: Failure, enabled = true) {
  const handlers = { onSaveAs: vi.fn(), onRenderAgain: vi.fn(), onDismiss: vi.fn() };
  const view = render(<ErrorBanner failure={failure} canSaveAs={enabled} canRenderAgain={enabled} {...handlers} />);
  return { ...handlers, view };
}

describe("ErrorBanner", () => {
  it("shows the one-line summary and the details only on request (D23)", () => {
    renderBanner(RENDER_FAILED);
    expect(screen.getByTestId("error").textContent).toBe(RENDER_FAILED.message);
    expect(screen.queryByTestId("error-details")).toBeNull();
    const toggle = screen.getByRole("button", { name: "Show details" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(screen.getByTestId("error-details").textContent).toBe(RENDER_FAILED.details);
    fireEvent.click(screen.getByRole("button", { name: "Hide details" }));
    expect(screen.queryByTestId("error-details")).toBeNull();
  });

  it("hides the details again for a new failure", () => {
    const { view } = renderBanner(RENDER_FAILED);
    fireEvent.click(screen.getByRole("button", { name: "Show details" }));
    view.rerender(
      <ErrorBanner
        failure={{ ...RENDER_FAILED }}
        canSaveAs
        onSaveAs={vi.fn()}
        canRenderAgain
        onRenderAgain={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("error-details")).toBeNull();
  });

  it("offers no details toggle for a refusal, and Dismiss calls back (F109)", () => {
    const { onDismiss } = renderBanner({ code: "read_failed", message: "Could not read project.json: denied." });
    expect(screen.queryByTestId("error-details-toggle")).toBeNull();
    expect(screen.queryByTestId("banner-save-as")).toBeNull();
    expect(screen.queryByTestId("banner-render-again")).toBeNull();
    fireEvent.click(screen.getByTestId("dismiss-error"));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("offers Save As when the project folder is gone (D57)", () => {
    const { onSaveAs } = renderBanner({ code: "project_unavailable", message: "The project folder was moved." });
    fireEvent.click(screen.getByTestId("banner-save-as"));
    expect(onSaveAs).toHaveBeenCalledOnce();
  });

  it("disables Save As while it cannot run", () => {
    renderBanner({ code: "project_unavailable", message: "The project folder was moved." }, false);
    expect(screen.getByTestId<HTMLButtonElement>("banner-save-as").disabled).toBe(true);
  });

  it("offers Render again after a preview used an incomplete clip that the worker removed", () => {
    const { onRenderAgain } = renderBanner(CLIP_REMOVED);
    expect(screen.getByTestId("error").textContent).toBe(CLIP_REMOVED.message);
    fireEvent.click(screen.getByRole("button", { name: "Render again" }));
    expect(onRenderAgain).toHaveBeenCalledOnce();
  });

  it("disables Render again while the worker is unavailable", () => {
    renderBanner(CLIP_REMOVED, false);
    expect(screen.getByTestId<HTMLButtonElement>("banner-render-again").disabled).toBe(true);
  });

  it.each<[string, Failure]>([
    ["an export, which Export renders again", { ...CLIP_REMOVED, job: "export" }],
    ["another render failure", { ...RENDER_FAILED, job: "preview" }],
  ])("offers no Render again for %s", (_case, failure) => {
    renderBanner(failure);
    expect(screen.queryByTestId("banner-render-again")).toBeNull();
  });

  it("offers Render again for the worker's own message about a removed clip, not for one it could not remove", () => {
    // The worker's f-strings (supervisor.py) in source order: a clip it could not remove, then a clip it removed.
    const messages = Array.from(supervisor.matchAll(/f"(manim used an incomplete clip [^"]+)"/g), ([, text = ""]) =>
      text.replace(/\{[^}]+\}/g, "2.mp4"),
    );
    const offered = messages.map((message) => {
      const { view } = renderBanner({ ...CLIP_REMOVED, message });
      const shown = within(view.container).queryByTestId("banner-render-again") !== null;
      view.unmount();
      return shown;
    });
    expect(offered).toEqual([false, true]);
  });
});
