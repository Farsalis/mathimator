import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { formatSeconds } from "../format";
import { playbackAt, shouldSeek } from "../playback";
import type { PreviewIndex } from "../types";

interface PreviewStageProps {
  index: PreviewIndex | null;
  time: number;
  total: number;
  playing: boolean;
  onTime: (time: number) => void;
  onPlaying: (playing: boolean) => void;
}

export function PreviewStage({ index, time, total, playing, onTime, onPlaying }: PreviewStageProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const scrubbing = useRef(false);
  const [mediaNote, setMediaNote] = useState<string | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [missing, setMissing] = useState<ReadonlySet<string>>(new Set());
  const choice = playbackAt(index, time, (path) => !missing.has(path));
  const src = choice.path ? convertFileSrc(choice.path) : null;

  useEffect(() => {
    if (!src) {
      setBlobUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    void fetch(src)
      .then((response) => {
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        return response.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) {
          return;
        }
        objectUrl = URL.createObjectURL(new Blob([buffer], { type: "video/mp4" }));
        setBlobUrl(objectUrl);
        setMediaNote(null);
      })
      .catch(() => {
        if (!cancelled && choice.path) {
          const missingPath = choice.path;
          setMissing((current) => {
            const next = new Set(current);
            next.add(missingPath);
            return next;
          });
          setMediaNote(null);
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [src]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !blobUrl) {
      return;
    }
    const seek = (): void => {
      if (shouldSeek(video.currentTime, choice.time)) {
        video.currentTime = choice.time;
      }
    };
    if (video.readyState >= 1) {
      seek();
    } else {
      video.addEventListener("loadeddata", seek, { once: true });
    }
  }, [blobUrl, choice.time]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !blobUrl) {
      return;
    }
    if (playing) {
      void video.play().catch(() => onPlaying(false));
    } else {
      video.pause();
    }
  }, [playing, blobUrl, onPlaying]);

  function onTimeUpdate(): void {
    const video = videoRef.current;
    if (!video || scrubbing.current) {
      return;
    }
    switch (choice.mode) {
      case "movie":
        onTime(video.currentTime);
        return;
      case "partial":
        onTime(beatStart(index, choice.beatIndex) + video.currentTime);
        return;
      case "blank":
      case "empty":
        return;
      default: {
        const unexpected: never = choice;
        throw new Error(`Unhandled playback ${JSON.stringify(unexpected)}`);
      }
    }
  }

  function onEnded(): void {
    if (choice.mode !== "partial" || !index) {
      onPlaying(false);
      return;
    }
    const next = index.beats.find((beat) => beat.index === choice.beatIndex + 1);
    if (next && playing) {
      onTime(next.derivedStart);
      return;
    }
    onPlaying(false);
  }

  function onScrub(value: number): void {
    scrubbing.current = true;
    onTime(value);
  }

  const canPlay = choice.mode === "movie" || choice.mode === "partial";

  return (
    <section className="stage" aria-label="Preview">
      <div className="canvas">
        {blobUrl ? (
          <video
            ref={videoRef}
            data-testid="preview-video"
            src={blobUrl}
            preload="auto"
            playsInline
            onTimeUpdate={onTimeUpdate}
            onEnded={onEnded}
            onLoadedData={() => setMediaNote(null)}
            onError={() => setMediaNote("Could not load the preview file.")}
          />
        ) : src ? null : (
          <p className="empty">{choice.mode === "empty" ? "Add a beat to preview it here." : "No frame yet."}</p>
        )}
        {mediaNote ? <p className="media-note">{mediaNote}</p> : null}
        {choice.badge ? (
          <p className="badge" data-testid="preview-badge">
            Preview is out of date
          </p>
        ) : null}
      </div>
      <div className="transport">
        <button
          type="button"
          data-testid="play-button"
          onClick={() => onPlaying(!playing)}
          disabled={!canPlay || total <= 0}
        >
          {playing ? "Pause" : "Play"}
        </button>
        <input
          data-testid="playhead"
          aria-label="Playhead"
          type="range"
          min={0}
          max={Math.max(total, 0.001)}
          step={0.01}
          value={Math.min(time, Math.max(total, 0))}
          disabled={total <= 0}
          onInput={(event) => onScrub(Number(event.currentTarget.value))}
          onPointerUp={() => {
            scrubbing.current = false;
          }}
          onKeyUp={() => {
            scrubbing.current = false;
          }}
        />
        <span className="time" data-testid="playhead-time">
          {formatSeconds(time)} / {formatSeconds(total)}
        </span>
      </div>
    </section>
  );
}

function beatStart(index: PreviewIndex | null, beatIndex: number): number {
  const beat = index?.beats.find((item) => item.index === beatIndex);
  return beat ? beat.derivedStart : 0;
}
