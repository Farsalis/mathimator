import { useState } from "react";
import {
  FRAME_RATES,
  RESOLUTIONS,
  allowsTransparency,
  isExportFormat,
  presetDefaults,
  transparencyMessage,
  type ExportFormat,
  type ExportProfile,
} from "../exportProfile";

interface ExportPanelProps {
  initial: ExportProfile;
  canStart: boolean;
  onStart: (profile: ExportProfile) => void;
  onClose: () => void;
}

const FORMAT_LABELS: Record<ExportFormat, string> = {
  mp4: "MP4",
  gif: "GIF",
  webm: "WebM",
  png_sequence: "PNG sequence",
};

export function ExportPanel({ initial, canStart, onStart, onClose }: ExportPanelProps) {
  const [profile, setProfile] = useState(initial);
  const allows = allowsTransparency(profile.format);

  function chooseFormat(format: ExportFormat): void {
    const next = presetDefaults(format);
    if (allowsTransparency(format) && allowsTransparency(profile.format)) {
      next.transparent = profile.transparent;
    }
    setProfile(next);
  }

  return (
    <div className="export-panel" role="presentation">
      <form
        className="export-card"
        role="dialog"
        aria-label="Export"
        data-testid="export-panel"
        onSubmit={(event) => {
          event.preventDefault();
          if (!canStart) {
            return;
          }
          onStart({
            ...profile,
            transparent: allows && profile.transparent,
          });
        }}
      >
        <h2>Export</h2>
        <label>
          Preset
          <select
            data-testid="export-format"
            value={profile.format}
            onChange={(event) => {
              const value = event.target.value;
              if (isExportFormat(value)) {
                chooseFormat(value);
              }
            }}
          >
            {(Object.keys(FORMAT_LABELS) as ExportFormat[]).map((format) => (
              <option key={format} value={format}>
                {FORMAT_LABELS[format]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Resolution
          <select
            data-testid="export-resolution"
            value={`${profile.width}x${profile.height}`}
            onChange={(event) => {
              const match = RESOLUTIONS.find((item) => `${item.width}x${item.height}` === event.target.value);
              if (!match) {
                return;
              }
              setProfile((current) => ({ ...current, width: match.width, height: match.height }));
            }}
          >
            {RESOLUTIONS.map((item) => (
              <option key={item.label} value={`${item.width}x${item.height}`}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Frame rate
          <select
            data-testid="export-fps"
            value={String(profile.fps)}
            onChange={(event) => {
              const fps = Number(event.target.value);
              if (!FRAME_RATES.some((item) => item === fps)) {
                return;
              }
              setProfile((current) => ({ ...current, fps }));
            }}
          >
            {FRAME_RATES.map((fps) => (
              <option key={fps} value={fps}>
                {fps}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            data-testid="export-transparent"
            checked={allows && profile.transparent}
            disabled={!allows}
            onChange={(event) => {
              if (!allows) {
                return;
              }
              setProfile((current) => ({ ...current, transparent: event.target.checked }));
            }}
          />
          Transparent background
        </label>
        <p className="hint" data-testid="transparency-note">
          {transparencyMessage(profile.format)}
        </p>
        <div className="export-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
          <button type="submit" data-testid="export-start" disabled={!canStart}>
            Export
          </button>
        </div>
      </form>
    </div>
  );
}
