// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const MOVIE = "C:/Users/Ana/Sine/.mathimator/cache/MainScene.mp4";

/** A fresh index whose one beat plays `PARTIAL`. Each call is a new index object. */
function previewIndex(): PreviewIndex {
  const beat = { id: "beat_1", index: 0, derivedStart: 0, duration: 2, partial: PARTIAL, lastGood: null, fresh: true };
  return { output: null, beats: [beat] };
}

function stage(index: PreviewIndex) {
  return <PreviewStage index={index} time={0} total={2} playing={false} onTime={vi.fn()} onPlaying={vi.fn()} />;
}

/** Two fresh 2 s beats that share `PARTIAL`, plus the movie when `output` is given. */
function twoBeats(output: string | null): PreviewIndex {
  const beat = { id: "beat_1", index: 0, derivedStart: 0, duration: 2, partial: PARTIAL, lastGood: null, fresh: true };
  return { output, beats: [beat, { ...beat, id: "beat_2", index: 1, derivedStart: 2 }] };
}

/** Serves every preview file, and stands in for the media playback jsdom does not have. */
function serveFiles(): { play: ReturnType<typeof vi.fn> } {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response("mp4"))),
  );
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
  const play = vi.fn(() => Promise.resolve());
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(play);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  return { play };
}

/**
 * Makes `video` a loaded, playing element and records every seek. `report` sends a timeupdate at `time`, and `run`
 * moves the time on without one, as playback does between two timeupdates.
 */
function playingVideo(video: HTMLVideoElement): {
  seeks: number[];
  report: (time: number) => void;
  run: (time: number) => void;
} {
  const seeks: number[] = [];
  let now = 0;
  Object.defineProperty(video, "readyState", { configurable: true, value: 4 });
  Object.defineProperty(video, "paused", { configurable: true, value: false });
  Object.defineProperty(video, "currentTime", {
    configurable: true,
    get: () => now,
    set: (value: number) => {
      seeks.push(value);
      now = value;
    },
  });
  return {
    seeks,
    report: (time) => {
      now = time;
      fireEvent.timeUpdate(video);
    },
    run: (time) => {
      now = time;
    },
  };
}

describe("PreviewStage", () => {
  it("logs an unreadable preview file, shows blank plus the badge, and reads it again for a new index", async () => {
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

  it("does not seek the playing video to the time it reported, and seeks for a scrub", async () => {
    serveFiles();
    const onTime = vi.fn<(time: number) => void>();
    const index = twoBeats(MOVIE);
    const view = (time: number) => (
      <PreviewStage index={index} time={time} total={4} playing onTime={onTime} onPlaying={vi.fn()} />
    );
    const { rerender } = render(view(0));
    const video = await screen.findByTestId<HTMLVideoElement>("preview-video");
    const media = playingVideo(video);
    media.report(1.25);
    expect(onTime).toHaveBeenCalledExactlyOnceWith(1.25);
    media.run(1.3);
    rerender(view(1.25));
    // The seek the stage waited for at mount is gone with its effect.
    fireEvent(video, new Event("loadeddata"));
    expect(media.seeks).toEqual([]);
    rerender(view(3));
    expect(media.seeks).toEqual([45.5 / 15]);
  });

  it("moves the playhead to the frame the video paused on, after its last report", async () => {
    serveFiles();
    const onTime = vi.fn<(time: number) => void>();
    const index = twoBeats(MOVIE);
    const view = (playing: boolean) => (
      <PreviewStage index={index} time={1.25} total={4} playing={playing} onTime={onTime} onPlaying={vi.fn()} />
    );
    const { rerender } = render(view(true));
    const video = await screen.findByTestId<HTMLVideoElement>("preview-video");
    const media = playingVideo(video);
    media.report(1.25);
    media.run(1.4);
    rerender(view(false));
    // Pausing reports the time once more, but Play already shows.
    fireEvent.timeUpdate(video);
    expect(onTime).toHaveBeenCalledExactlyOnceWith(1.25);
    fireEvent.pause(video);
    expect(onTime).toHaveBeenCalledTimes(2);
    expect(onTime.mock.lastCall?.[0]).toBeCloseTo(1.4, 9);
  });

  it("plays again when the next beat plays the same file", async () => {
    const { play } = serveFiles();
    const onPlaying = vi.fn();
    const index = twoBeats(null);
    const view = (time: number) => (
      <PreviewStage index={index} time={time} total={4} playing onTime={vi.fn()} onPlaying={onPlaying} />
    );
    const { rerender } = render(view(1));
    await screen.findByTestId("preview-video");
    expect(play).toHaveBeenCalledTimes(1);
    rerender(view(2));
    expect(play).toHaveBeenCalledTimes(2);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  it("keeps the clock running over a beat with no file, and stops at the end", () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn((callback: FrameRequestCallback) => frames.push(callback)),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const onTime = vi.fn<(time: number) => void>();
    const onPlaying = vi.fn<(playing: boolean) => void>();
    const blank = { id: "beat_1", index: 0, derivedStart: 0, duration: 1, partial: null, lastGood: null, fresh: false };
    const view = (time: number) => (
      <PreviewStage
        index={{ output: null, beats: [blank] }}
        time={time}
        total={1}
        playing
        onTime={onTime}
        onPlaying={onPlaying}
      />
    );
    const { rerender } = render(view(0.2));
    const button = screen.getByTestId<HTMLButtonElement>("play-button");
    expect([button.disabled, button.textContent]).toEqual([false, "Pause"]);
    const tick = (now: number): void => act(() => frames.at(-1)?.(now));
    tick(1000);
    tick(1250);
    expect(onTime.mock.lastCall?.[0]).toBeCloseTo(0.45, 9);
    rerender(view(0.9));
    tick(1450);
    expect(onTime).toHaveBeenLastCalledWith(1);
    expect(onPlaying).toHaveBeenCalledExactlyOnceWith(false);
  });
});
