// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PreviewIndex } from "../types";
import { PreviewStage } from "./PreviewStage";

vi.mock("@tauri-apps/api/core", () => ({ convertFileSrc: (path: string) => path }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PARTIAL = "C:/Users/Ana/Sine/.mathimator/cache/partial_0.mp4";

/** A fresh index whose one beat plays `PARTIAL`. Each call is a new index object. */
function previewIndex(): PreviewIndex {
  const beat = { id: "beat_1", index: 0, derivedStart: 0, duration: 2, partial: PARTIAL, lastGood: null, fresh: true };
  return { output: null, beats: [beat] };
}

function stage(index: PreviewIndex) {
  return <PreviewStage index={index} time={0} total={2} playing={false} onTime={vi.fn()} onPlaying={vi.fn()} />;
}

describe("PreviewStage", () => {
  it("logs an unreadable preview file, shows blank plus the badge, and reads it again for a new index (D23, F115)", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockReturnValue(new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    const index = previewIndex();
    const { rerender } = render(stage(index));
    await waitFor(() => expect(screen.getByTestId("preview-badge")).toBeTruthy());
    expect(screen.getByText("No frame yet.")).toBeTruthy();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(`Could not read the preview file ${PARTIAL}:`, new Error("HTTP 404"));

    rerender(stage(index));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    rerender(stage(previewIndex()));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(PARTIAL);
    expect(screen.queryByTestId("preview-badge")).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
