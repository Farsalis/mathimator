import { useRef } from "react";
import { clampDuration, formatSeconds } from "../format";
import type { Beat } from "../types";

interface TimelineProps {
  beats: Beat[];
  durations: number[];
  starts: number[];
  dirty: boolean[];
  selectedBeatId: string | null;
  playhead: number;
  total: number;
  /** The worker is not ready, so durations cannot be changed (C9.5). */
  disabled: boolean;
  onSelect: (beatId: string) => void;
  onDraft: (beatId: string, duration: number) => void;
  onCommit: (beatId: string, duration: number) => void;
}

export function Timeline({
  beats,
  durations,
  starts,
  dirty,
  selectedBeatId,
  playhead,
  total,
  disabled,
  onSelect,
  onDraft,
  onCommit,
}: TimelineProps) {
  return (
    <section className="timeline" aria-label="Timeline" data-testid="timeline">
      {beats.length === 0 ? (
        <p className="timeline-empty">No beats yet. Add axes, a formula, or text.</p>
      ) : (
        <ol>
          {beats.map((beat, index) => (
            <BeatCell
              key={beat.id}
              beat={beat}
              beats={beats}
              duration={durations[index] ?? beat.duration}
              start={starts[index] ?? 0}
              dirty={dirty[index] === true}
              selected={beat.id === selectedBeatId}
              active={
                playhead >= (starts[index] ?? 0) &&
                (index === beats.length - 1 || playhead < (starts[index + 1] ?? total))
              }
              disabled={disabled}
              onSelect={onSelect}
              onDraft={onDraft}
              onCommit={onCommit}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function BeatCell({
  beat,
  beats,
  duration,
  start,
  dirty,
  selected,
  active,
  disabled,
  onSelect,
  onDraft,
  onCommit,
}: {
  beat: Beat;
  beats: Beat[];
  duration: number;
  start: number;
  dirty: boolean;
  selected: boolean;
  active: boolean;
  disabled: boolean;
  onSelect: (beatId: string) => void;
  onDraft: (beatId: string, duration: number) => void;
  onCommit: (beatId: string, duration: number) => void;
}) {
  const drag = useRef<{ startX: number; startDuration: number } | null>(null);

  return (
    <li
      className={selected ? "beat selected" : active ? "beat active" : "beat"}
      data-testid={`beat-${beat.id}`}
      data-dirty={dirty ? "true" : "false"}
    >
      <button type="button" className="beat-body" onClick={() => onSelect(beat.id)}>
        <span className="beat-start">{formatSeconds(start)}</span>
        <span className="beat-action">{beat.action}</span>
        {dirty ? <span className="dirty-mark">dirty</span> : null}
      </button>
      <button
        type="button"
        className="duration-handle"
        aria-label={`Duration of ${beat.id}`}
        disabled={disabled}
        onPointerDown={(event) => {
          event.stopPropagation();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { startX: event.clientX, startDuration: duration };
        }}
        onPointerMove={(event) => {
          const current = drag.current;
          if (!current) {
            return;
          }
          const next = clampDuration(beats, beat.id, current.startDuration + (event.clientX - current.startX) / 48);
          onDraft(beat.id, next);
        }}
        onPointerUp={(event) => {
          const current = drag.current;
          if (!current) {
            return;
          }
          drag.current = null;
          const next = clampDuration(beats, beat.id, current.startDuration + (event.clientX - current.startX) / 48);
          onCommit(beat.id, next);
        }}
      >
        {formatSeconds(duration)}
      </button>
    </li>
  );
}
