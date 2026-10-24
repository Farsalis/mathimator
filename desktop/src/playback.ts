import type { EditorState, IndexBeat, PreviewIndex } from "./types";

export type Playback =
  | { mode: "empty"; badge: false; path: null; time: number; beatIndex: null }
  | { mode: "blank"; badge: true; path: null; time: number; beatIndex: number }
  | { mode: "movie"; badge: false; path: string; time: number; beatIndex: number }
  | { mode: "partial"; badge: boolean; path: string; time: number; beatIndex: number };

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
  const allFresh = index.beats.every((item) => item.fresh);
  if (allFresh && exists(index.output, fileExists)) {
    return {
      mode: "movie",
      badge: false,
      path: index.output,
      time: Math.round(clamped * 1000) / 1000,
      beatIndex: beat.index,
    };
  }
  if (beat.fresh && exists(beat.partial, fileExists)) {
    return {
      mode: "partial",
      badge: !allFresh,
      path: beat.partial,
      time: offset,
      beatIndex: beat.index,
    };
  }
  if (exists(beat.lastGood, fileExists)) {
    return {
      mode: "partial",
      badge: true,
      path: beat.lastGood,
      time: offset,
      beatIndex: beat.index,
    };
  }
  return { mode: "blank", badge: true, path: null, time: offset, beatIndex: beat.index };
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

/** The beat playing at `time`, or the last beat past the end. Undefined only when there are no beats. */
function beatAt(beats: readonly IndexBeat[], time: number): IndexBeat | undefined {
  return beats.find((beat) => time < beat.derivedStart + beat.duration) ?? beats.at(-1);
}
