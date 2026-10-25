// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkerUnavailable } from "./WorkerUnavailable";

afterEach(cleanup);

const REASON = "The installed runtime is incomplete (python). Reinstall Mathimator.";

describe("WorkerUnavailable", () => {
  it("shows the reason, and Retry is disabled until the retry settles (D31)", async () => {
    let finish: () => void = () => undefined;
    const retried = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const onRetry = vi.fn(() => retried);
    render(<WorkerUnavailable reason={REASON} onRetry={onRetry} />);
    expect(screen.getByTestId("unavailable-reason").textContent).toBe(REASON);
    const retry = screen.getByTestId<HTMLButtonElement>("retry-worker");
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledOnce();
    expect(retry.disabled).toBe(true);
    finish();
    await act(() => retried);
    expect(retry.disabled).toBe(false);
  });

  it("leaves out an empty reason", () => {
    render(<WorkerUnavailable reason="" onRetry={() => Promise.resolve()} />);
    expect(screen.queryByTestId("unavailable-reason")).toBeNull();
    expect(screen.getByTestId("retry-worker")).toBeTruthy();
  });
});
