// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Failure } from "../workerQueue";
import { ErrorBanner } from "./ErrorBanner";

afterEach(cleanup);

const RENDER_FAILED: Failure = {
  code: "render_failed",
  message: "manim exited with status 1: RuntimeError: boom",
  details: "Traceback (most recent call last):\nRuntimeError: boom",
};

function renderBanner(failure: Failure, canSaveAs = true) {
  const handlers = { onSaveAs: vi.fn(), onDismiss: vi.fn() };
  const view = render(<ErrorBanner failure={failure} canSaveAs={canSaveAs} {...handlers} />);
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
    view.rerender(<ErrorBanner failure={{ ...RENDER_FAILED }} canSaveAs onSaveAs={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.queryByTestId("error-details")).toBeNull();
  });

  it("offers no details toggle for a refusal, and Dismiss calls back (F109)", () => {
    const { onDismiss } = renderBanner({ code: "read_failed", message: "Could not read project.json: denied." });
    expect(screen.queryByTestId("error-details-toggle")).toBeNull();
    expect(screen.queryByTestId("banner-save-as")).toBeNull();
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
});
