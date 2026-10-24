import type { EditorState, IndexBeat, PreviewIndex } from "./types";

/** The preview profile's frame rate, which is fixed (spec §1). */
export const PREVIEW_FPS = 15;

export type Playback =
  | { mode: "empty"; badge: false; path: null; time: number; beatIndex: null }
  | { mode: "blank"; badge: true; path: null; time: number; beatIndex: number }
  | { mode: "movie"; badge: false; path: string; time: number; beatIndex: number }
  | { mode: "partial"; badge: boolean; path: string; time: number; beatIndex: number };

/** A Playback that plays a file. */
export type FilePlayback = Extract<Playback, { path: string }>;

/**
 * What the canvas shows at timeline `time` (spec §6). This is the one scrub rule: the worker has none (D44). For a
 * file, `time` is the middle of the frame to show, in the movie or in the beat's own partial movie: a seek to the edge
 * of a frame can show the frame before it, which may belong to the beat before.
 */
export function playbackAt(
  index: PreviewIndex | null,
  time: number,
  fileExists: (path: string) => boolean = () => true,
): Playback {
  const clamped = time < 0 ? 0 : time;
  const beat = index ? beatAt(index.beats, clamped) : undefined;
  if (!index || !beat) {
    return { mode: "empty", badge: false, path: null, time: 0, beatIndex: null };
  }
  const offset = Math.max(0, Math.round((clamped - beat.derivedStart) * 1000) / 1000);
  /** The middle of the frame at `offset`, in the beat's own partial movie. */
  const clipTime = (frameIn(beat, offset) + 0.5) / PREVIEW_FPS;
  const allFresh = index.beats.every((item) => item.fresh);
  if (allFresh && exists(index.output, fileExists)) {
    return {
      mode: "movie",
      badge: false,
      path: index.output,
      time: framesOf(beat).first / PREVIEW_FPS + clipTime,
      beatIndex: beat.index,
    };
  }
  if (beat.fresh && exists(beat.partial, fileExists)) {
    return {
      mode: "partial",
      badge: !allFresh,
      path: beat.partial,
      time: clipTime,
      beatIndex: beat.index,
    };
  }
  if (exists(beat.lastGood, fileExists)) {
    return {
      mode: "partial",
      badge: true,
      path: beat.lastGood,
      time: clipTime,
      beatIndex: beat.index,
    };
  }
  return { mode: "blank", badge: true, path: null, time: offset, beatIndex: beat.index };
}

/**
 * The timeline time at `currentTime` in the file `choice` plays, the inverse of playbackAt (G3.2). In the movie it is
 * in the beat whose frames hold `currentTime`, so the timeline highlights the beat on screen, and it never passes the
 * end of the timeline.
 */
export function playheadAt(index: PreviewIndex, choice: FilePlayback, currentTime: number): number {
  const beat =
    choice.mode === "movie"
      ? lastStarted(index.beats, currentTime, (item) => framesOf(item).first / PREVIEW_FPS)
      : index.beats.find((item) => item.index === choice.beatIndex);
  if (!beat) {
    return 0;
  }
  const { first, count } = framesOf(beat);
  // The share of the beat's frames before `currentTime`. A partial movie starts at its beat's first frame.
  const played = (currentTime * PREVIEW_FPS - (choice.mode === "movie" ? first : 0)) / count;
  return beat.derivedStart + beat.duration * Math.min(Math.max(played, 0), 1);
}

export function shouldSeek(currentTime: number, requested: number): boolean {
  return Math.abs(currentTime - requested) > 0.001;
}

export function patchBeat(current: EditorState | null, beatIndex: number, path: string): EditorState | null {
  if (!current?.index) {
    return current;
  }
  return {
    ...current,
    index: {
      ...current.index,
      output: null,
      beats: current.index.beats.map((beat) =>
        beat.index === beatIndex ? { ...beat, partial: path, fresh: true } : beat,
      ),
    },
  };
}

function exists(path: string | null, fileExists: (path: string) => boolean): path is string {
  return path !== null && path.length > 0 && fileExists(path);
}

/**
 * The beat playing at `time`: the last one that has started, by the same rule as the timeline highlight. A time on a
 * boundary is the next beat's start, not the end of the one before it (G3.4). Undefined only when there are no beats.
 */
function beatAt(beats: readonly IndexBeat[], time: number): IndexBeat | undefined {
  return lastStarted(beats, time, (beat) => beat.derivedStart);
}

/** The last beat whose start, by `startOf`, is not after `time`. */
function lastStarted(
  beats: readonly IndexBeat[],
  time: number,
  startOf: (beat: IndexBeat) => number,
): IndexBeat | undefined {
  const next = beats.findIndex((beat) => startOf(beat) > time);
  return beats[next === -1 ? beats.length - 1 : Math.max(next - 1, 0)];
}

/**
 * The frames of `beat` in the movie: `count` frames from `first`. Generated code snaps every beat to whole frames,
 * rounding each boundary half up (D63), so every frame rate lines up with the timeline.
 */
function framesOf(beat: IndexBeat): { first: number; count: number } {
  const first = frameAt(beat.derivedStart);
  return { first, count: frameAt(beat.derivedStart + beat.duration) - first };
}

/** The beat's frame `offset` seconds into it, counted from its first frame. */
function frameIn(beat: IndexBeat, offset: number): number {
  const { count } = framesOf(beat);
  return Math.min(Math.floor((milliseconds(offset) * count) / milliseconds(beat.duration)), count - 1);
}

/**
 * The frame a beat boundary at `seconds` falls on. Times are whole milliseconds, so this is integer arithmetic and a
 * half frame rounds up exactly: in floating point, 4.1 × 15 comes to 61.4999… and would round down.
 */
function frameAt(seconds: number): number {
  return Math.floor((milliseconds(seconds) * PREVIEW_FPS + 500) / 1000);
}

function milliseconds(seconds: number): number {
  return Math.round(seconds * 1000);
}
