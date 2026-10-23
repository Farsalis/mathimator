import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { derivedStarts } from "./format";
import type { ExportProfile } from "./exportProfile";
import {
  armExportCancel,
  beginExport,
  beginPreview,
  createHandshake,
  markExportSent,
  receive,
  requestCancel,
  type Handshake,
} from "./jobSession";
import { patchBeat } from "./playback";
import { parseEditorState, parseWorkerEvent, type Edit, type EditorState, type WorkerEvent } from "./types";

const PREVIEW_DEBOUNCE_MS = 300;

export type Selection = { kind: "none" } | { kind: "beat"; id: string } | { kind: "object"; id: string };

export function useEditor() {
  const [ready, setReady] = useState(false);
  const [state, setState] = useState<EditorState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [status, setStatus] = useState("Starting");
  const [selection, setSelection] = useState<Selection>({ kind: "none" });
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [codeOpen, setCodeOpen] = useState(false);
  const [codeText, setCodeText] = useState("");
  const [noticesOpen, setNoticesOpen] = useState(false);
  const [presetOpen, setPresetOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportLabel, setExportLabel] = useState("Export");
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [exportPath, setExportPath] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const jobs = useRef<Handshake>(createHandshake());
  const armingExport = useRef(false);
  const notedReady = useRef(false);
  const epoch = useRef(0);
  const debounce = useRef<number | null>(null);
  const stateRef = useRef<EditorState | null>(null);

  useLayoutEffect(() => {
    stateRef.current = state;
  }, [state]);

  async function refreshState(): Promise<boolean> {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        const value = await invoke<unknown>("get_state");
        const parsed = parseEditorState(value);
        if (parsed) {
          setState(parsed);
          setStatus((current) => (current === "Starting" ? "Ready" : current));
          return true;
        }
      } catch {
        // The supervisor may still be starting.
      }
      await delay(100);
    }
    return false;
  }

  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    let stopped = false;

    function publish(next: Handshake): void {
      jobs.current = next;
      setStatus(next.status);
      setRendering(next.rendering);
      setExporting(next.exporting);
      setExportLabel(next.exportLabel);
      setExportNote(next.exportNote);
      setExportPath(next.exportPath);
      if (next.error) {
        setError(next.error);
      }
    }

    function onEvent(message: WorkerEvent): void {
      if (message.event === "ready") {
        setReady(true);
      }
      const previewDone = message.event === "done" && message.jobId === jobs.current.previewJobId;
      const step = receive(jobs.current, message);
      publish(step.session);
      applyPlayback(step.applied, previewDone);
    }

    function applyPlayback(applied: WorkerEvent[], previewDone: boolean): void {
      for (const event of applied) {
        if (event.event === "beat_ready") {
          setState((current) => patchBeat(current, event.beatIndex, event.path));
        }
      }
      if (previewDone) {
        void refreshState();
      }
    }

    void (async () => {
      unlisten = await listen<unknown>("worker-event", (event) => {
        const parsed = parseWorkerEvent(event.payload);
        if (parsed) {
          onEvent(parsed);
        }
      });
      if (stopped) {
        unlisten();
        return;
      }
      const loaded = await refreshState();
      if (!loaded && !stopped) {
        setError("The project did not load.");
        setStatus("Worker unavailable");
      }
      try {
        await invoke("worker_status");
      } catch (reason: unknown) {
        if (!stopped) {
          setError(reason instanceof Error ? reason.message : String(reason));
          setStatus("Worker unavailable");
        }
      }
    })();

    return () => {
      stopped = true;
      if (debounce.current !== null) {
        window.clearTimeout(debounce.current);
      }
      if (unlisten) {
        unlisten();
      }
    };
  }, []);

  useEffect(() => {
    if (ready && state && !notedReady.current) {
      notedReady.current = true;
      void invoke("note_shell_ready");
    }
  }, [ready, state]);

  function schedulePreview(): void {
    epoch.current += 1;
    const token = epoch.current;
    jobs.current = { ...jobs.current, epoch: token };
    if (debounce.current !== null) {
      window.clearTimeout(debounce.current);
    }
    debounce.current = window.setTimeout(() => {
      debounce.current = null;
      if (token !== epoch.current) {
        return;
      }
      void (async () => {
        try {
          const jobId = await invoke<string>("reserve_job_id", { prefix: "preview" });
          if (token !== epoch.current) {
            return;
          }
          const step = beginPreview(jobs.current, jobId, token);
          jobs.current = step.session;
          setStatus(step.session.status);
          setRendering(step.session.rendering);
          for (const event of step.applied) {
            if (event.event === "beat_ready") {
              setState((current) => patchBeat(current, event.beatIndex, event.path));
            }
          }
          await invoke("render_preview", { jobId });
        } catch (reason: unknown) {
          setRendering(false);
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })();
    }, PREVIEW_DEBOUNCE_MS);
  }

  async function commit(edit: Edit, preview: boolean): Promise<void> {
    try {
      const value = await invoke<unknown>("edit_document", { edit });
      const rejected = parseWorkerEvent(value);
      if (rejected?.event === "rejected") {
        setError(rejected.message);
        if (edit.op === "set_expression" || edit.op === "set_curve_component" || edit.op === "set_resolution") {
          setFieldError(rejected.message);
        }
        return;
      }
      const parsed = parseEditorState(value);
      if (!parsed) {
        setError("The edit did not return a document.");
        return;
      }
      setState(parsed);
      setError(null);
      setFieldError(null);
      if (parsed.focus?.beatId) {
        setSelection({ kind: "beat", id: parsed.focus.beatId });
      }
      if (preview) {
        schedulePreview();
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function undo(): Promise<void> {
    await runHistory("undo_document");
  }

  async function redo(): Promise<void> {
    await runHistory("redo_document");
  }

  async function runHistory(command: "undo_document" | "redo_document"): Promise<void> {
    try {
      const value = await invoke<unknown>(command);
      const rejected = parseWorkerEvent(value);
      if (rejected?.event === "rejected") {
        setError(rejected.message);
        return;
      }
      const parsed = parseEditorState(value);
      if (!parsed) {
        setError("History did not return a document.");
        return;
      }
      setState(parsed);
      setError(null);
      setDrafts({});
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function onExport(): void {
    if (jobs.current.exporting || armingExport.current) {
      setExportNote("An export is already running.");
      return;
    }
    setExportNote(null);
    setPresetOpen(true);
  }

  function onStartExport(profile: ExportProfile): void {
    if (jobs.current.exporting || armingExport.current) {
      setExportNote("An export is already running.");
      return;
    }
    armingExport.current = true;
    setExportLabel("Rendering export");
    setExporting(true);
    setExportNote(null);
    setError(null);
    setStatus("Rendering export");
    setPresetOpen(false);
    const edit: Edit = {
      op: "set_export_profile",
      format: profile.format,
      width: profile.width,
      height: profile.height,
      fps: profile.fps,
      transparent: profile.transparent,
    };
    void (async () => {
      try {
        const jobId = await invoke<string>("reserve_job_id", { prefix: "export" });
        const begun = beginExport(jobs.current, jobId);
        jobs.current = begun.session;
        setStatus(begun.session.status);
        setExporting(begun.session.exporting);
        setExportLabel(begun.session.exportLabel);
        setExportPath(begun.session.exportPath);
        setExportNote(begun.session.exportNote);
        for (const event of begun.applied) {
          if (event.event === "beat_ready") {
            setState((current) => patchBeat(current, event.beatIndex, event.path));
          }
        }
        if (!begun.session.exporting) {
          armingExport.current = false;
          return;
        }
        const value = await invoke<unknown>("edit_document", { edit });
        const rejected = parseWorkerEvent(value);
        if (rejected?.event === "rejected") {
          jobs.current = {
            ...jobs.current,
            exportJobId: null,
            exporting: false,
            exportLabel: "Export",
            status: "Ready",
            error: rejected.message,
          };
          armingExport.current = false;
          setExporting(false);
          setExportLabel("Export");
          setStatus("Ready");
          setError(rejected.message);
          return;
        }
        const parsed = parseEditorState(value);
        if (parsed) {
          setState(parsed);
        }
        if (jobs.current.pendingCancel) {
          jobs.current = {
            ...jobs.current,
            exportJobId: null,
            pendingCancel: false,
            exporting: false,
            exportCommandSent: false,
            exportLabel: "Export",
            status: "Cancelled",
          };
          armingExport.current = false;
          setExporting(false);
          setExportLabel("Export");
          setStatus("Cancelled");
          return;
        }
        await invoke("export_movie", { jobId });
        const sent = markExportSent(jobs.current);
        jobs.current = sent.session;
        armingExport.current = false;
        if (sent.cancelJobId) {
          await invoke("cancel_render", { jobId: sent.cancelJobId });
        }
      } catch (reason: unknown) {
        jobs.current = {
          ...jobs.current,
          exportJobId: null,
          exporting: false,
          exportLabel: "Export",
          status: "Ready",
        };
        armingExport.current = false;
        setExporting(false);
        setExportLabel("Export");
        setStatus("Ready");
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }

  function onCancelExport(): void {
    if (armingExport.current && jobs.current.exportJobId === null) {
      jobs.current = armExportCancel(jobs.current);
      return;
    }
    const step = requestCancel(jobs.current);
    jobs.current = step.session;
    if (step.cancelJobId === null) {
      return;
    }
    void invoke("cancel_render", { jobId: step.cancelJobId }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : String(reason));
    });
  }

  async function onClearCache(): Promise<void> {
    try {
      const value = await invoke<unknown>("clear_cache");
      const rejected = parseWorkerEvent(value);
      if (rejected?.event === "rejected") {
        setError(rejected.message);
        return;
      }
      const parsed = parseEditorState(value);
      if (!parsed) {
        setError("Clear cache did not return a document.");
        return;
      }
      setState(parsed);
      setError(null);
      setExportPath(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function onNotices(): void {
    setCodeOpen(false);
    setNoticesOpen(true);
  }

  async function onOpenSample(): Promise<void> {
    try {
      const value = await invoke<unknown>("open_sample");
      const rejected = parseWorkerEvent(value);
      if (rejected?.event === "rejected") {
        setError(rejected.message);
        return;
      }
      const parsed = parseEditorState(value);
      if (!parsed) {
        setError("The reference-60 sample did not open.");
        return;
      }
      setState(parsed);
      setSelection({ kind: "none" });
      setDrafts({});
      setPlayhead(0);
      setPlaying(false);
      setCodeOpen(false);
      setNoticesOpen(false);
      setError(null);
      setExportPath(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function onViewCode(): Promise<void> {
    setNoticesOpen(false);
    setCodeOpen(true);
    try {
      const value = await invoke<unknown>("view_code");
      if (isRecord(value) && value.event === "code" && typeof value.source === "string") {
        setCodeText(value.source);
        return;
      }
      const rejected = parseWorkerEvent(value);
      if (rejected?.event === "rejected") {
        setError(rejected.message);
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function onExportCode(): Promise<void> {
    try {
      const value = await invoke<unknown>("export_code_dialog");
      if (isRecord(value) && value.cancelled === true) {
        return;
      }
      if (isRecord(value) && typeof value.path === "string") {
        setExportNote(`Code exported to ${value.path}`);
        setError(null);
        return;
      }
      const rejected = parseWorkerEvent(value);
      if (rejected?.event === "rejected") {
        setError(rejected.message);
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function onOpen(): Promise<void> {
    try {
      const value = await invoke<unknown>("open_project_dialog");
      if (isRecord(value) && value.cancelled === true) {
        return;
      }
      const rejected = parseWorkerEvent(value);
      if (rejected?.event === "rejected") {
        setError(rejected.message);
        return;
      }
      const parsed = parseEditorState(value);
      if (!parsed) {
        setError("That folder is not a Mathimator project.");
        return;
      }
      setState(parsed);
      setSelection({ kind: "none" });
      setDrafts({});
      setPlayhead(0);
      setPlaying(false);
      setCodeOpen(false);
      setError(null);
      setExportPath(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function onDraftDuration(beatId: string, duration: number): void {
    setDrafts((current) => ({ ...current, [beatId]: duration }));
  }

  function onCommitDuration(beatId: string, duration: number): void {
    const beat = stateRef.current?.document.scene.beats.find((item) => item.id === beatId);
    setDrafts((current) => {
      const next = { ...current };
      delete next[beatId];
      return next;
    });
    if (!beat || Math.abs(beat.duration - duration) < 0.0005) {
      return;
    }
    void commit({ op: "set_beat_duration", beatId, duration }, true);
  }

  const beats = state?.document.scene.beats ?? [];
  const durations = beats.map((beat) => drafts[beat.id] ?? beat.duration);
  const dragging = Object.keys(drafts).length > 0;
  const starts = dragging ? derivedStarts(durations) : (state?.derived.starts ?? []);
  const total = dragging
    ? Math.round(durations.reduce((sum, value) => sum + value, 0) * 1000) / 1000
    : (state?.derived.total ?? 0);

  return {
    ready,
    state,
    error,
    fieldError,
    status,
    selection,
    setSelection,
    drafts,
    playhead,
    setPlayhead,
    playing,
    setPlaying,
    codeOpen,
    setCodeOpen,
    codeText,
    noticesOpen,
    setNoticesOpen,
    presetOpen,
    setPresetOpen,
    exporting,
    exportLabel,
    exportNote,
    exportPath,
    rendering,
    starts,
    total,
    commit,
    undo,
    redo,
    onExport,
    onStartExport,
    onCancelExport,
    onClearCache,
    onNotices,
    onOpenSample,
    onViewCode,
    onExportCode,
    onOpen,
    onDraftDuration,
    onCommitDuration,
  };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
