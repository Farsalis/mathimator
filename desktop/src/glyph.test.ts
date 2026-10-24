import { describe, expect, it } from "vitest";
import { applyGlyph, NO_GLYPH, parseGlyph, UNSUPPORTED, type GlyphView } from "./glyph";

const SHOWN: GlyphView = { svgPath: "C:/Users/Ana/Sine/.mathimator/equation/formula-eq_1-4.svg", message: null };

describe("applyGlyph (C9.9, F139)", () => {
  it("shows a compiled SVG and clears the message", () => {
    const svgPath = "C:/Users/Ana/Sine/.mathimator/equation/formula-eq_1-5.svg";
    expect(applyGlyph({ svgPath: null, message: UNSUPPORTED }, { status: "ok", svgPath })).toEqual({
      svgPath,
      message: null,
    });
  });

  it("keeps the last good SVG on an error and shows the message", () => {
    const message = "typst compile timed out";
    expect(applyGlyph(SHOWN, { status: "error", message })).toEqual({ ...SHOWN, message });
  });

  it("clears the SVG for a formula outside the subset", () => {
    expect(applyGlyph(SHOWN, { status: "unsupported" })).toEqual({ svgPath: null, message: UNSUPPORTED });
    expect(UNSUPPORTED).toBe("No live preview for this formula.");
  });

  it("changes nothing for a cancelled compile", () => {
    expect(applyGlyph(SHOWN, { status: "cancelled" })).toBe(SHOWN);
    expect(applyGlyph(NO_GLYPH, { status: "cancelled" })).toBe(NO_GLYPH);
  });
});

describe("parseGlyph", () => {
  it.each([
    [{ status: "ok", svgPath: "C:/a.svg" }],
    [{ status: "unsupported" }],
    [{ status: "error", message: "Typst is unavailable: not found" }],
    [{ status: "cancelled" }],
  ])("reads %j", (reply) => {
    expect(parseGlyph(reply)).toEqual(reply);
  });

  it.each([
    ["no object", "ok"],
    ["no status", { svgPath: "C:/a.svg" }],
    ["an unknown status", { status: "done" }],
    ["ok without a path", { status: "ok" }],
    ["a path that is not a string", { status: "ok", svgPath: 3 }],
    ["error without a message", { status: "error" }],
    ["an array", [{ status: "ok", svgPath: "C:/a.svg" }]],
  ])("rejects %s", (_, reply) => {
    expect(parseGlyph(reply)).toBeNull();
  });
});
