import { describe, expect, it } from "vitest";
import timing from "../../tests/fixtures/timing/frame-boundaries.json";
import { derivedStarts } from "./format";
import {
  patchBeat,
  PREVIEW_FPS,
  playbackAt,
  playheadAt,
  shouldSeek,
  type FilePlayback,
  type Playback,
} from "./playback";
import type { EditorState, IndexBeat, PreviewIndex } from "./types";

const MOVIE = "C:/Users/Ana/Sine/.mathimator/cache/MainScene.mp4";
const LAST_GOOD = "C:/Users/Ana/Sine/.mathimator/cache/last-good/partial-1.mp4";

/** A finished preview of beats with `durations`: every beat fresh with its partial movie, plus the movie. */
function finished(durations: readonly number[]): PreviewIndex {
  const starts = derivedStarts(durations);
  const beats = durations.map((duration, index): IndexBeat => ({
    id: `beat_${index + 1}`,
    index,
    derivedStart: starts[index] ?? 0,
    duration,
    partial: partialOf(index),
    lastGood: null,
    fresh: true,
  }));
  return { output: MOVIE, beats };
}

function partialOf(index: number): string {
  return `C:/Users/Ana/Sine/.mathimator/cache/partial-${index}.mp4`;
}

/** `index` after an edit to beat index 0 since it rendered: its partial is still on disk, and so is the movie. */
function dirtied(index: PreviewIndex, lastGood: string | null = null): PreviewIndex {
  return {
    ...index,
    beats: index.beats.map((beat) => (beat.index === 0 ? { ...beat, fresh: false, lastGood } : beat)),
  };
}

/** The preview's frame at `seconds` into a file. */
function frameOf(seconds: number): number {
  return Math.floor(seconds * PREVIEW_FPS);
}

/** The frames of each beat in the movie, [first, end): every boundary snapped to the nearest frame, halves up (D63). */
function movieFrames(index: PreviewIndex): Array<[number, number]> {
  const boundary = (seconds: number): number => Math.floor((Math.round(seconds * 1000) * PREVIEW_FPS + 500) / 1000);
  return index.beats.map((beat) => [boundary(beat.derivedStart), boundary(beat.derivedStart + beat.duration)]);
}

/** The beat the timeline highlights at `time`: the last one whose derived start is not after it. */
function highlighted(index: PreviewIndex, time: number): number {
  return index.beats.filter((beat) => beat.derivedStart <= time).length - 1;
}

function played(choice: Playback): FilePlayback {
  if (choice.path === null) {
    throw new Error(`Expected a file, got ${choice.mode}.`);
  }
  return choice;
}

describe("scrub (D44: the rule that ships)", () => {
  // Ported from tests/test_phase2.py, which checked the worker's scrub command before D44 removed it.
  const reference60 = [8, 10, 6, 4, 8, 8, 8, 8];

  it("shows the movie frame of beat index 1 at 9.0 s on a finished reference-60 preview (spec §2)", () => {
    const choice = playbackAt(finished(reference60), 9);
    expect(choice).toMatchObject({ mode: "movie", path: MOVIE, badge: false, beatIndex: 1 });
    expect(frameOf(choice.time)).toBe(9 * PREVIEW_FPS);
  });

  it("shows the last good partial with the badge once that beat is dirty, and a blank canvas without one", () => {
    const index = finished(reference60);
    const dirty = index.beats.map((beat) =>
      beat.index === 1 ? { ...beat, fresh: false, partial: null, lastGood: LAST_GOOD } : beat,
    );
    const choice = playbackAt({ ...index, beats: dirty }, 9);
    expect(choice).toMatchObject({ mode: "partial", path: LAST_GOOD, badge: true, beatIndex: 1 });
    expect(frameOf(choice.time)).toBe(PREVIEW_FPS);

    const never = dirty.map((beat) => (beat.index === 1 ? { ...beat, lastGood: null } : beat));
    expect(playbackAt({ ...index, beats: never }, 9)).toMatchObject({ mode: "blank", badge: true, beatIndex: 1 });
  });

  it.each([
    [[0.1, 0.2, 0.5], 0.3],
    [[1.1, 2.2, 1], 3.3],
  ])("takes a playhead on a boundary as the start of the next beat (%j at %s s, G3.4)", (durations, time) => {
    const index = finished(durations);
    const partial = playbackAt({ ...index, output: null }, time);
    expect(partial).toMatchObject({ mode: "partial", path: index.beats[2]?.partial, beatIndex: 2 });
    expect(frameOf(partial.time)).toBe(0);
    const movie = playbackAt(index, time);
    expect(movie.beatIndex).toBe(2);
    expect(frameOf(movie.time)).toBe(movieFrames(index)[2]?.[0]);
  });

  // The worker's tests check every boundary of the fixture; the preview plays only at PREVIEW_FPS.
  it.each(timing.boundaries.filter(({ fps }) => fps === PREVIEW_FPS))(
    "snaps a boundary at $seconds s to frame $frame from its exact time, not its binary approximation (D63)",
    ({ seconds, frame }) => {
      // Each falls on a half frame, which rounds up: 4.1 × 15 is 61.5, frame 62, but 61.4999… in floating point.
      const index = finished([seconds, 1]);
      const movie = played(playbackAt(index, seconds));
      expect(movie.beatIndex).toBe(1);
      expect(frameOf(movie.time)).toBe(frame);
      expect(frameOf(playbackAt(index, seconds - 0.001).time)).toBe(frame - 1);
      expect(highlighted(index, playheadAt(index, movie, (frame - 0.5) / PREVIEW_FPS))).toBe(0);
      expect(highlighted(index, playheadAt(index, movie, (frame + 0.5) / PREVIEW_FPS))).toBe(1);
    },
  );

  it("shows each beat over the frames the worker's scene draws for it (D63)", () => {
    const { durations, fps, frames } = timing.beats;
    expect(fps).toBe(PREVIEW_FPS);
    const index = finished(durations);
    let first = 0;
    for (const beat of index.beats) {
      const count = frames[beat.index] ?? 0;
      expect(frameOf(playbackAt(index, beat.derivedStart).time)).toBe(first);
      expect(frameOf(playbackAt(index, beat.derivedStart + beat.duration - 0.001).time)).toBe(first + count - 1);
      first += count;
    }
  });

  it("shows a movie frame of the highlighted beat everywhere on beats that are not whole frames (G3.2)", () => {
    const index = finished(Array.from({ length: 20 }, () => 0.5));
    const frames = movieFrames(index);
    for (let step = 0; step <= 1000; step += 1) {
      const time = step / 100;
      const choice = playbackAt(index, time);
      const beat = highlighted(index, time);
      const [first, end] = frames[beat] ?? [0, 0];
      expect(choice.beatIndex).toBe(beat);
      expect(frameOf(choice.time)).toBeGreaterThanOrEqual(first);
      expect(frameOf(choice.time)).toBeLessThan(end);
    }
  });

  it("shows the last frame at the end of the timeline, never the end of the file", () => {
    const index = finished([0.5, 0.5]);
    expect(movieFrames(index)).toEqual([
      [0, 8],
      [8, 15],
    ]);
    expect(frameOf(playbackAt(index, 1).time)).toBe(14);
    expect(frameOf(playbackAt({ ...index, output: null }, 1).time)).toBe(6);
  });
});

describe("playhead from the playing video", () => {
  it("follows the movie into the beat whose frames are on screen, and maps back to the same frame (G3.2)", () => {
    const index = finished(Array.from({ length: 20 }, () => 0.5));
    const frames = movieFrames(index);
    const movie = played(playbackAt(index, 0));
    const total = frames.at(-1)?.[1] ?? 0;
    for (let frame = 0; frame < total; frame += 1) {
      const playhead = playheadAt(index, movie, (frame + 0.5) / PREVIEW_FPS);
      const beat = highlighted(index, playhead);
      const [first, end] = frames[beat] ?? [0, 0];
      expect(frame).toBeGreaterThanOrEqual(first);
      expect(frame).toBeLessThan(end);
      expect(frameOf(playbackAt(index, playhead).time)).toBe(frame);
    }
    expect(playheadAt(index, movie, total / PREVIEW_FPS)).toBe(10);
  });

  it("adds the time in a partial movie to its beat's start, up to the beat's end", () => {
    const index = { ...finished([2, 3]), output: null };
    const partial = played(playbackAt(index, 2.5));
    expect(partial.beatIndex).toBe(1);
    expect(playheadAt(index, partial, 1.5)).toBeCloseTo(3.5, 9);
    expect(playheadAt(index, partial, 3.2)).toBe(5);
  });
});

describe("playback (F147)", () => {
  it("shows neither the movie nor the stale partial of a dirty beat, but a blank canvas with the badge", () => {
    expect(playbackAt(dirtied(finished([2, 2])), 0.2)).toEqual({
      mode: "blank",
      badge: true,
      path: null,
      time: 0.2,
      beatIndex: 0,
    });
  });

  it("shows a dirty beat's last good partial with the badge", () => {
    expect(playbackAt(dirtied(finished([2, 2]), LAST_GOOD), 0.2)).toMatchObject({
      mode: "partial",
      badge: true,
      path: LAST_GOOD,
      beatIndex: 0,
    });
  });

  it("badges the fresh partial of another beat while one is dirty", () => {
    expect(playbackAt(dirtied(finished([2, 2])), 2.2)).toMatchObject({
      mode: "partial",
      badge: true,
      path: partialOf(1),
      beatIndex: 1,
    });
  });

  it("falls back to the beat partial when the movie file is missing", () => {
    expect(playbackAt(finished([2, 2]), 0.2, (path) => path !== MOVIE)).toMatchObject({
      mode: "partial",
      badge: false,
      path: partialOf(0),
      beatIndex: 0,
    });
  });

  it("shows a blank canvas with the badge when the partial file is missing", () => {
    expect(playbackAt({ ...finished([2, 2]), output: null }, 2.2, () => false)).toMatchObject({
      mode: "blank",
      badge: true,
      path: null,
      beatIndex: 1,
    });
  });

  it("holds the last beat past the end and shows nothing without beats", () => {
    expect(playbackAt(finished([2, 2]), 9).beatIndex).toBe(1);
    expect(playbackAt({ output: null, beats: [] }, 1).mode).toBe("empty");
    expect(playbackAt(null, 1).mode).toBe("empty");
  });

  it("marks a beat fresh with its new partial and drops the stale movie when a partial arrives", () => {
    const state: EditorState = {
      event: "state",
      projectPath: "C:/Users/Ana/Sine",
      document: {
        name: "Sine",
        scene: { kind: "scene", camera: null, objects: [], beats: [], templateInstances: [] },
        exportProfile: { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false },
      },
      derived: { starts: [0, 2], total: 4, dirty: [true, false], canUndo: false, canRedo: false },
      index: dirtied(finished([2, 2])),
      source: null,
    };
    const next = patchBeat(state, 0, "C:/new/partial-0.mp4");
    expect(next?.index?.output).toBeNull();
    expect(next?.index?.beats[0]).toMatchObject({ fresh: true, partial: "C:/new/partial-0.mp4" });
    expect(next?.index?.beats[1]).toEqual(state.index?.beats[1]);
  });

  it("seeks for any scrub over a millisecond", () => {
    expect(shouldSeek(1, 1.05)).toBe(true);
    expect(shouldSeek(1, 1.0001)).toBe(false);
  });
});
