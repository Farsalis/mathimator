import { describe, expect, it } from "vitest";
import fixture from "../../tests/fixtures/protocol/export-choices.json";
import {
  clampDuration,
  derivedStarts,
  formatSeconds,
  MAX_TOTAL_SECONDS,
  MIN_BEAT_SECONDS,
  parseDecimal,
  roundMs,
} from "./format";
import type { Beat } from "./types";

/** Beats with `durations`, named beat_1, beat_2 and so on. */
function beats(...durations: number[]): Beat[] {
  return durations.map((duration, index) => ({
    id: `beat_${index + 1}`,
    duration,
    action: "wait",
    targets: [],
  }));
}

describe("duration limits (spec §4, F174)", () => {
  it("are the worker's", () => {
    expect(MIN_BEAT_SECONDS).toBe(fixture.minBeatSeconds);
    expect(MAX_TOTAL_SECONDS).toBe(fixture.maxTotalSeconds);
  });
});

describe("clampDuration", () => {
  it.each([0.04, 0, -3])("raises %s s to the shortest beat", (next) => {
    expect(clampDuration(beats(2), "beat_1", next)).toBe(MIN_BEAT_SECONDS);
  });

  it("caps a beat at the longest timeline less the other beats, whatever its own duration", () => {
    expect(clampDuration(beats(2, 100), "beat_1", 50)).toBe(20);
    expect(clampDuration(beats(119, 1), "beat_2", 5)).toBe(1);
    expect(clampDuration(beats(119), "beat_1", 130)).toBe(MAX_TOTAL_SECONDS);
  });

  it("keeps the shortest beat when the other beats already fill the timeline", () => {
    expect(clampDuration(beats(119.95, 2), "beat_2", 1)).toBe(MIN_BEAT_SECONDS);
  });

  it("rounds to the millisecond", () => {
    expect(clampDuration(beats(2), "beat_1", 1.23456)).toBe(1.235);
    expect(clampDuration(beats(1.1, 2.2), "beat_1", 117.8)).toBe(117.8);
  });
});

describe("derivedStarts", () => {
  it("starts each beat where the beats before it end, to the millisecond", () => {
    expect(derivedStarts([])).toEqual([]);
    expect(derivedStarts([1.1, 2.2, 0.3])).toEqual([0, 1.1, 3.3]);
  });
});

describe("formatSeconds", () => {
  it.each([
    [2, "2.0s"],
    [1.5, "1.5s"],
    [10, "10.0s"],
    [0, "0.0s"],
    [0.1234, "0.123s"],
    [2.0004, "2.0s"],
  ])("writes %s as %s", (value, text) => {
    expect(formatSeconds(value)).toBe(text);
  });
});

describe("roundMs", () => {
  it.each([
    [0.1 + 0.2, 0.3],
    [1.23449, 1.234],
    [1.2346, 1.235],
    [120, 120],
  ])("rounds %s to %s", (seconds, rounded) => {
    expect(roundMs(seconds)).toBe(rounded);
  });
});

describe("parseDecimal (D24, F045)", () => {
  it.each([
    ["1.5", 1.5],
    ["2,5", 2.5],
    [" -3 ", -3],
    ["+.5", 0.5],
    [",25", 0.25],
    ["4.", 4],
    ["0", 0],
  ])("reads %j as %s", (text, value) => {
    expect(parseDecimal(text)).toBe(value);
  });

  it.each(["", "   ", "1e3", "0x10", "1.2.3", "1,5,0", "abc", "Infinity", "NaN", "-", ".", "1 000"])(
    "refuses %j",
    (text) => {
      expect(parseDecimal(text)).toBeNull();
    },
  );
});
