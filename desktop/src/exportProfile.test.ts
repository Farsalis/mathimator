import { describe, expect, it } from "vitest";
import documentSource from "../../worker/mathimator_worker/document.py?raw";
import {
  allowsTransparency,
  asExportProfile,
  EXPORT_FORMATS,
  FRAME_RATES,
  GIF_TRANSPARENCY,
  isExportFormat,
  MP4_TRANSPARENCY,
  presetDefaults,
  RESOLUTIONS,
  transparencyMessage,
  type ExportProfile,
} from "./exportProfile";
import { ParseError } from "./parse";

const PROFILE: ExportProfile = { format: "webm", width: 1280, height: 720, fps: 60, transparent: true };

/** Every match of `pattern` in what the worker's document.py assigns to `name`. */
function workerValues(name: string, pattern: RegExp): Set<string> {
  const value = new RegExp(`^${name} = (.+)$`, "m").exec(documentSource)?.[1] ?? "";
  return new Set(Array.from(value.matchAll(pattern), ([match]) => match));
}

describe("export choices (spec §1, F174)", () => {
  it("offers the formats, sizes and frame rates the worker accepts", () => {
    expect(new Set(EXPORT_FORMATS)).toEqual(workerValues("EXPORT_FORMATS", /(?<=")\w+(?=")/g));
    expect(new Set(RESOLUTIONS.map(({ width, height }) => `(${width}, ${height})`))).toEqual(
      workerValues("EXPORT_SIZES", /\(\d+, \d+\)/g),
    );
    expect(new Set(FRAME_RATES.map(String))).toEqual(workerValues("EXPORT_FPS", /\d+/g));
  });

  it("refuses transparency in MP4 and GIF with the worker's words", () => {
    expect(EXPORT_FORMATS.filter(allowsTransparency)).toEqual(["webm", "png_sequence"]);
    expect(transparencyMessage("mp4")).toBe(MP4_TRANSPARENCY);
    expect(transparencyMessage("gif")).toBe(GIF_TRANSPARENCY);
    expect(documentSource).toContain(`"${MP4_TRANSPARENCY}"`);
    expect(documentSource).toContain(`"${GIF_TRANSPARENCY}"`);
    expect(transparencyMessage("webm")).toBe("Transparency includes the background.");
    expect(transparencyMessage("png_sequence")).toBe("Transparency includes the background.");
  });
});

describe("presetDefaults", () => {
  it.each([
    ["mp4", 1920, 1080, 30],
    ["gif", 854, 480, 15],
    ["webm", 1920, 1080, 30],
    ["png_sequence", 1920, 1080, 30],
  ] as const)("starts %s at %s×%s, %s fps, opaque", (format, width, height, fps) => {
    expect(presetDefaults(format)).toEqual({ format, width, height, fps, transparent: false });
  });
});

describe("isExportFormat", () => {
  it.each(["mp4", "gif", "webm", "png_sequence"])("accepts %j", (value) => {
    expect(isExportFormat(value)).toBe(true);
  });

  it.each(["MP4", "png", "", 4, null, undefined])("refuses %j", (value) => {
    expect(isExportFormat(value)).toBe(false);
  });
});

describe("asExportProfile (D50)", () => {
  it("reads a profile", () => {
    expect(asExportProfile({ ...PROFILE }, "document.exportProfile")).toEqual(PROFILE);
  });

  it.each([
    ["a list", [], "document.exportProfile"],
    ["an unknown format", { ...PROFILE, format: "avi" }, "document.exportProfile.format"],
    ["a fractional width", { ...PROFILE, width: 1280.5 }, "document.exportProfile.width"],
    ["a missing height", { ...PROFILE, height: undefined }, "document.exportProfile.height"],
    ["a frame rate as text", { ...PROFILE, fps: "60" }, "document.exportProfile.fps"],
    ["transparency as text", { ...PROFILE, transparent: "yes" }, "document.exportProfile.transparent"],
  ])("refuses %s, naming the field", (_, value, field) => {
    expect(() => asExportProfile(value, "document.exportProfile")).toThrow(ParseError);
    expect(() => asExportProfile(value, "document.exportProfile")).toThrow(expect.objectContaining({ field }));
  });
});
