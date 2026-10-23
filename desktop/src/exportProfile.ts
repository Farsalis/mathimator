export type ExportFormat = "mp4" | "gif" | "webm" | "png_sequence";

export interface ExportProfile {
  format: ExportFormat;
  width: number;
  height: number;
  fps: number;
  transparent: boolean;
}

export const EXPORT_FORMATS: readonly ExportFormat[] = ["mp4", "gif", "webm", "png_sequence"];

export const RESOLUTIONS: readonly { width: number; height: number; label: string }[] = [
  { width: 854, height: 480, label: "854×480" },
  { width: 1280, height: 720, label: "1280×720" },
  { width: 1920, height: 1080, label: "1920×1080" },
];

export const FRAME_RATES: readonly number[] = [15, 30, 60];

export const MP4_TRANSPARENCY = "MP4 cannot store transparency.";
export const GIF_TRANSPARENCY = "GIF cannot store an alpha channel.";

const PRESET_DEFAULTS: Record<ExportFormat, { width: number; height: number; fps: number }> = {
  mp4: { width: 1920, height: 1080, fps: 30 },
  gif: { width: 854, height: 480, fps: 15 },
  webm: { width: 1920, height: 1080, fps: 30 },
  png_sequence: { width: 1920, height: 1080, fps: 30 },
};

export function allowsTransparency(format: ExportFormat): boolean {
  switch (format) {
    case "mp4":
    case "gif":
      return false;
    case "webm":
    case "png_sequence":
      return true;
    default: {
      const unexpected: never = format;
      throw new Error(`Unhandled export format ${String(unexpected)}`);
    }
  }
}

export function transparencyMessage(format: ExportFormat): string {
  switch (format) {
    case "mp4":
      return MP4_TRANSPARENCY;
    case "gif":
      return GIF_TRANSPARENCY;
    case "webm":
    case "png_sequence":
      return "Transparency includes the background.";
    default: {
      const unexpected: never = format;
      throw new Error(`Unhandled export format ${String(unexpected)}`);
    }
  }
}

export function presetDefaults(format: ExportFormat): ExportProfile {
  const preset = PRESET_DEFAULTS[format];
  return {
    format,
    width: preset.width,
    height: preset.height,
    fps: preset.fps,
    transparent: false,
  };
}

export function isExportFormat(value: unknown): value is ExportFormat {
  return typeof value === "string" && EXPORT_FORMATS.some((item) => item === value);
}

export function parseExportProfile(value: unknown): ExportProfile | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (!isExportFormat(record.format)) {
    return null;
  }
  if (
    typeof record.width !== "number" ||
    typeof record.height !== "number" ||
    typeof record.fps !== "number" ||
    typeof record.transparent !== "boolean"
  ) {
    return null;
  }
  return {
    format: record.format,
    width: record.width,
    height: record.height,
    fps: record.fps,
    transparent: record.transparent,
  };
}
