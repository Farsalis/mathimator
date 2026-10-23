import type { Beat } from "./types";

export function derivedStarts(durations: number[]): number[] {
  const starts: number[] = [];
  let cursor = 0;
  for (const duration of durations) {
    starts.push(Math.round(cursor * 1000) / 1000);
    cursor += duration;
  }
  return starts;
}

export function formatSeconds(value: number): string {
  const rounded = Math.round(value * 1000) / 1000;
  const text = rounded.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (text.includes(".")) {
    return `${text}s`;
  }
  return `${text}.0s`;
}

export function clampDuration(beats: Beat[], beatId: string, next: number): number {
  const quantized = Math.round(next * 1000) / 1000;
  const others = beats.reduce((sum, beat) => sum + (beat.id === beatId ? 0 : beat.duration), 0);
  const max = Math.max(0.1, Math.round((120 - others) * 1000) / 1000);
  return Math.min(max, Math.max(0.1, quantized));
}

export function beatAt(starts: number[], durations: number[], time: number): number {
  if (durations.length === 0) {
    return 0;
  }
  for (let index = 0; index < durations.length; index += 1) {
    const end = starts[index] + durations[index];
    if (time < end) {
      return index;
    }
  }
  return durations.length - 1;
}
