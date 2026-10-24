import { useEffect, useEffectEvent, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { formatSeconds } from "../format";
import { playbackAt, playheadAt, shouldSeek } from "../playback";
import type { PreviewIndex } from "../types";

const NONE: ReadonlySet<string> = new Set();

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
  /** The playhead the playing video last reported. The video is already there, so it is not seeked there (F043). */
  const reported = useRef<number | null>(null);
  const [mediaNote, setMediaNote] = useState<string | null>(null);
  /** The files that could not be read for `index`. Any new index tries them again (F115). */
  const [missing, setMissing] = useState<{ index: PreviewIndex | null; paths: ReadonlySet<string> }>({
    index: null,
    paths: NONE,
  });
  /** The last file read for the canvas. It stays on screen while the next file loads (F123). */
  const [loaded, setLoaded] = useState<{ path: string; url: string } | null>(null);
  const absent = missing.index === index ? missing.paths : NONE;
  const choice = playbackAt(index, time, (path) => !absent.has(path));
  const path = choice.path;
  if (path === null && loaded !== null) {
    // A beat with no file shows a blank canvas, not the last file.
    setLoaded(null);
  }
  const blobUrl = path === null ? null : (loaded?.url ?? null);
  /** Whether the video holds the file under the playhead, so it may be seeked and played. */
  const current = loaded !== null && loaded.path === path;

  /** An unreadable file is logged, and the canvas falls back to last-good or blank with the badge (D23). */
  const onUnreadable = useEffectEvent((unreadable: string, reason: unknown) => {
    console.warn(`Could not read the preview file ${unreadable}:`, reason);
    setMissing({ index, paths: new Set([...absent, unreadable]) });
    setMediaNote(null);
  });

  /** Moves the playhead on by `seconds` while no file plays, and stops at the end (D49). */
  const advance = useEffectEvent((seconds: number) => {
    const next = time + seconds;
    if (next < total) {
      onTime(next);
      return;
    }
    onTime(total);
    onPlaying(false);
  });

  useEffect(() => {
    if (path === null) {
      return;
    }
    let cancelled = false;
    void fetch(convertFileSrc(path))
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) {
          return;
        }
        setLoaded({ path, url: URL.createObjectURL(new Blob([buffer], { type: "video/mp4" })) });
        setMediaNote(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          onUnreadable(path, reason);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  useEffect(() => {
    if (!loaded) {
      return;
    }
    return () => URL.revokeObjectURL(loaded.url);
  }, [loaded]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !current || (!video.paused && time === reported.current)) {
      return;
    }
    const seek = (): void => {
      if (shouldSeek(video.currentTime, choice.time)) {
        video.currentTime = choice.time;
      }
    };
    if (video.readyState >= 1) {
      seek();
      return;
    }
    video.addEventListener("loadeddata", seek, { once: true });
    return () => video.removeEventListener("loadeddata", seek);
  }, [current, blobUrl, time, choice.time]);

  // A beat change plays again: two beats can share one file, and the video that ended waits paused at its end (F111).
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !current) {
      return;
    }
    if (playing) {
      void video.play().catch(() => onPlaying(false));
    } else {
      video.pause();
    }
  }, [playing, current, blobUrl, choice.beatIndex, onPlaying]);

  // Over a beat with no file, a clock moves the playhead until the next file plays (D49).
  const playsFile = path !== null;
  useEffect(() => {
    if (!playing || playsFile) {
      return;
    }
    let last: number | null = null;
    let frame = 0;
    const tick = (now: number): void => {
      if (last !== null) {
        advance((now - last) / 1000);
      }
      last = now;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, playsFile]);

  /**
   * Moves the playhead to the frame the video shows. A paused video stops after its last report, and the report that
   * pausing sends arrives when Play already shows, so the pause event moves the playhead as well.
   */
  function follow(): void {
    const video = videoRef.current;
    if (!video || !index || choice.path === null || !current || scrubbing.current) {
      return;
    }
    const next = playheadAt(index, choice, video.currentTime);
    reported.current = next;
    onTime(next);
  }

  /** While it plays, the playhead follows the video. The seeks of a paused video come from the playhead. */
  function onTimeUpdate(): void {
    if (playing) {
      follow();
    }
  }

  function onEnded(): void {
    reported.current = null;
    const next =
      choice.mode === "partial" ? index?.beats.find((beat) => beat.index === choice.beatIndex + 1) : undefined;
    if (next && playing) {
      onTime(next.derivedStart);
      return;
    }
    onPlaying(false);
  }

  function onScrub(value: number): void {
    scrubbing.current = true;
    reported.current = null;
    onTime(value);
  }

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
            onPause={follow}
            onEnded={onEnded}
            onLoadedData={() => setMediaNote(null)}
            onError={() => setMediaNote("Could not load the preview file.")}
          />
        ) : path !== null ? null : (
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
          disabled={!playing && total <= 0}
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
