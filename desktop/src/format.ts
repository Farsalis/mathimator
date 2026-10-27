import type { Beat } from "./types";

/** The shortest beat, in seconds (spec §4). The worker refuses a shorter one, by document.py's MIN_BEAT_SECONDS. */
export const MIN_BEAT_SECONDS = 0.1;

/** The longest timeline, in seconds (spec §4). The worker refuses a longer one, by document.py's MAX_TOTAL_SECONDS. */
export const MAX_TOTAL_SECONDS = 120;

/** Plain decimal notation, with a dot or a comma as the decimal point: no exponent, hex, or other forms. */
const DECIMAL = /^[+-]?(\d+([.,]\d*)?|[.,]\d+)$/;

/** `seconds` rounded to the millisecond, the precision of the times the timeline shows and edits. */
export function roundMs(seconds: number): number {
  return Math.round(seconds * 1000) / 1000;
}

export function derivedStarts(durations: readonly number[]): number[] {
  const starts: number[] = [];
  let cursor = 0;
  for (const duration of durations) {
    starts.push(roundMs(cursor));
    cursor += duration;
  }
  return starts;
}

export function formatSeconds(value: number): string {
  const text = roundMs(value).toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (text.includes(".")) {
    return `${text}s`;
  }
  return `${text}.0s`;
}

/** The number typed in a number field, or null for an empty field or anything but a decimal. 2,5 means 2.5 (D24). */
export function parseDecimal(text: string): number | null {
  const trimmed = text.trim();
  return DECIMAL.test(trimmed) ? Number(trimmed.replace(",", ".")) : null;
}

/** The duration `next` for beat `beatId`, in milliseconds and inside both limits given the other beats. */
export function clampDuration(beats: Beat[], beatId: string, next: number): number {
  const others = beats.reduce((sum, beat) => sum + (beat.id === beatId ? 0 : beat.duration), 0);
  const max = Math.max(MIN_BEAT_SECONDS, roundMs(MAX_TOTAL_SECONDS - others));
  return Math.min(max, Math.max(MIN_BEAT_SECONDS, roundMs(next)));
}
