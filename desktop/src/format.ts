import type { Beat } from "./types";

/** Plain decimal notation, with a dot or a comma as the decimal point: no exponent, hex, or other forms. */
const DECIMAL = /^[+-]?(\d+([.,]\d*)?|[.,]\d+)$/;

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

/** The number typed in a number field, or null for an empty field or anything but a decimal. 2,5 means 2.5 (D24). */
export function parseDecimal(text: string): number | null {
  const trimmed = text.trim();
  return DECIMAL.test(trimmed) ? Number(trimmed.replace(",", ".")) : null;
}

export function clampDuration(beats: Beat[], beatId: string, next: number): number {
  const quantized = Math.round(next * 1000) / 1000;
  const others = beats.reduce((sum, beat) => sum + (beat.id === beatId ? 0 : beat.duration), 0);
  const max = Math.max(0.1, Math.round((120 - others) * 1000) / 1000);
  return Math.min(max, Math.max(0.1, quantized));
}
