import { describe, expect, it } from "vitest";
import { PREVIEW_FPS, playbackAt, playheadAt, type FilePlayback, type Playback } from "./playback";
import type { IndexBeat, PreviewIndex } from "./types";

const MOVIE = "C:/Users/Ana/Sine/.mathimator/cache/MainScene.mp4";
const LAST_GOOD = "C:/Users/Ana/Sine/.mathimator/cache/last-good/partial-1.mp4";

/** A finished preview of beats with `durations`: every beat fresh with its partial movie, plus the movie. */
function finished(durations: readonly number[]): PreviewIndex {
  let cursor = 0;
  const beats = durations.map((duration, index): IndexBeat => {
    const beat = {
      id: `beat_${index + 1}`,
      index,
      derivedStart: Math.round(cursor * 1000) / 1000,
      duration,
      partial: `C:/Users/Ana/Sine/.mathimator/cache/partial-${index}.mp4`,
      lastGood: null,
      fresh: true,
    };
    cursor += duration;
    return beat;
  });
  return { output: MOVIE, beats };
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

  it("snaps a boundary from its exact time, not its binary approximation (D63)", () => {
    // 4.1 × 15 is 61.5, so beat index 2 starts on frame 62. In floating point it comes to 61.4999…, frame 61.
    const index = finished([2, 2.1, 1]);
    const movie = played(playbackAt(index, 4.1));
    expect(movie.beatIndex).toBe(2);
    expect(frameOf(movie.time)).toBe(62);
    expect(frameOf(playbackAt(index, 4.099).time)).toBe(61);
    expect(highlighted(index, playheadAt(index, movie, 61.5 / PREVIEW_FPS))).toBe(1);
    expect(highlighted(index, playheadAt(index, movie, 62.5 / PREVIEW_FPS))).toBe(2);
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
