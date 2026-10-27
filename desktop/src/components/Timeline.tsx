import { useRef } from "react";
import { KINDS, type Family } from "../beatKinds";
import { beatLabel } from "../beatRules";
import { clampDuration, formatSeconds } from "../format";
import type { Beat, SceneObject } from "../types";

interface TimelineProps {
  beats: Beat[];
  /** The objects whose names label the beats. */
  objects: SceneObject[];
  durations: number[];
  starts: number[];
  dirty: boolean[];
  selectedBeatId: string | null;
  playhead: number;
  total: number;
  /** The worker is not ready, so beats cannot be added and durations cannot be changed (C9.5). */
  disabled: boolean;
  onSelect: (beatId: string) => void;
  onDraft: (beatId: string, duration: number) => void;
  onCommit: (beatId: string, duration: number) => void;
  /** "+ Beat": adds a beat after the selected beat, on the selected object (design §10.3). */
  onAddBeat: () => void;
}

export function Timeline({
  beats,
  objects,
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
  onAddBeat,
}: TimelineProps) {
  return (
    <section className="timeline" aria-label="Timeline" data-testid="timeline">
      <div className="timeline-toolbar">
        <button type="button" data-testid="add-beat" disabled={disabled} onClick={onAddBeat}>
          + Beat
        </button>
      </div>
      {beats.length === 0 ? (
        <p className="timeline-empty">No beats yet. Add an object, or use + Beat.</p>
      ) : (
        <ol>
          {beats.map((beat, index) => (
            <BeatCell
              key={beat.id}
              beat={beat}
              beats={beats}
              label={beatLabel(beat, objects)}
              family={KINDS[beat.action].family}
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
  label,
  family,
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
  label: string;
  family: Family;
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
  const drag = useRef<{ pointerId: number; startX: number; startDuration: number } | null>(null);

  /** The drag of `pointerId`, or null for any other pointer, such as a second finger. */
  function dragOf(pointerId: number): { startX: number; startDuration: number } | null {
    return drag.current?.pointerId === pointerId ? drag.current : null;
  }

  /**
   * A drag that ends without a release, for example when the browser takes it over, goes back to the duration it
   * started from: committing that value only drops the draft (D48, F110).
   */
  function cancelDrag(pointerId: number): void {
    const current = dragOf(pointerId);
    if (!current) {
      return;
    }
    drag.current = null;
    onCommit(beat.id, current.startDuration);
  }

  return (
    <li
      className={selected ? "beat selected" : active ? "beat active" : "beat"}
      data-testid={`beat-${beat.id}`}
      data-dirty={dirty ? "true" : "false"}
      data-family={family}
    >
      <button type="button" className="beat-body" onClick={() => onSelect(beat.id)}>
        <span className="beat-start">{formatSeconds(start)}</span>
        <span className="beat-action">{label}</span>
        {dirty ? <span className="dirty-mark">dirty</span> : null}
      </button>
      <button
        type="button"
        className="duration-handle"
        aria-label={`Duration of ${beat.id}`}
        disabled={disabled}
        onPointerDown={(event) => {
          event.stopPropagation();
          if (drag.current) {
            // A second pointer does not take over the drag in progress.
            return;
          }
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { pointerId: event.pointerId, startX: event.clientX, startDuration: duration };
        }}
        onPointerMove={(event) => {
          const current = dragOf(event.pointerId);
          if (!current) {
            return;
          }
          const next = clampDuration(beats, beat.id, current.startDuration + (event.clientX - current.startX) / 48);
          onDraft(beat.id, next);
        }}
        onPointerUp={(event) => {
          const current = dragOf(event.pointerId);
          if (!current) {
            return;
          }
          drag.current = null;
          const next = clampDuration(beats, beat.id, current.startDuration + (event.clientX - current.startX) / 48);
          onCommit(beat.id, next);
        }}
        onPointerCancel={(event) => cancelDrag(event.pointerId)}
        onLostPointerCapture={(event) => cancelDrag(event.pointerId)}
      >
        {formatSeconds(duration)}
      </button>
    </li>
  );
}
