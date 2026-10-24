import { isRecord } from "./parse";

export const UNSUPPORTED = "No live preview for this formula.";

/** The reply of `compile_equation_preview` (C9.9). */
export type GlyphResult =
  | { status: "ok"; svgPath: string }
  | { status: "unsupported" }
  | { status: "error"; message: string }
  | { status: "cancelled" };

/** What the equation preview shows: the last good SVG and a message under it. */
export interface GlyphView {
  svgPath: string | null;
  message: string | null;
}

export const NO_GLYPH: GlyphView = { svgPath: null, message: null };

/** The view after `result`. An error keeps the previous SVG; a cancelled compile changes nothing (C9.9). */
export function applyGlyph(view: GlyphView, result: GlyphResult): GlyphView {
  switch (result.status) {
    case "ok":
      return { svgPath: result.svgPath, message: null };
    case "unsupported":
      return { svgPath: null, message: UNSUPPORTED };
    case "error":
      return { ...view, message: result.message };
    case "cancelled":
      return view;
    default: {
      const unexpected: never = result;
      throw new Error(`Unhandled glyph status ${JSON.stringify(unexpected)}`);
    }
  }
}

/** Reads a GlyphResult, or null when the reply does not have that shape. */
export function parseGlyph(value: unknown): GlyphResult | null {
  if (!isRecord(value) || typeof value.status !== "string") {
    return null;
  }
  switch (value.status) {
    case "ok":
      return typeof value.svgPath === "string" ? { status: "ok", svgPath: value.svgPath } : null;
    case "unsupported":
      return { status: "unsupported" };
    case "error":
      return typeof value.message === "string" ? { status: "error", message: value.message } : null;
    case "cancelled":
      return { status: "cancelled" };
    default:
      return null;
  }
}
