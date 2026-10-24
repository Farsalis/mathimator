import { describe, expect, it } from "vitest";
import { parseDecimal } from "./format";

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
