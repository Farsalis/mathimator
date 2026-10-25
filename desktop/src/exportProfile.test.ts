import { describe, expect, it } from "vitest";
import fixture from "../../tests/fixtures/protocol/export-choices.json";
import {
  allowsTransparency,
  asExportProfile,
  EXPORT_FORMATS,
  FRAME_RATES,
  isExportFormat,
  presetDefaults,
  RESOLUTIONS,
  transparencyMessage,
  type ExportProfile,
} from "./exportProfile";
import { ParseError } from "./parse";

const PROFILE: ExportProfile = { format: "webm", width: 1280, height: 720, fps: 60, transparent: true };

describe("export choices (spec §1, F174)", () => {
  it("offers the formats, sizes and frame rates the worker accepts", () => {
    expect(new Set(EXPORT_FORMATS)).toEqual(new Set(fixture.formats));
    expect(new Set(RESOLUTIONS.map(({ width, height }) => `${width}x${height}`))).toEqual(
      new Set(fixture.sizes.map(([width, height]) => `${width}x${height}`)),
    );
    expect(new Set(FRAME_RATES)).toEqual(new Set(fixture.fps));
  });

  it("refuses transparency in MP4 and GIF with the worker's words", () => {
    const opaque = EXPORT_FORMATS.filter((format) => !allowsTransparency(format));
    expect(Object.fromEntries(opaque.map((format) => [format, transparencyMessage(format)]))).toEqual(
      fixture.opaqueReasons,
    );
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
