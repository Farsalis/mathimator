import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";
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
import { asString, fieldsOf } from "./parse";
import { patchBeat } from "./playback";
import { parseEditorState, parseWorkerEvent, type Edit, type EditorState, type WorkerEvent } from "./types";
import { errorMessage, invokeWorker, WorkerFailure, WorkerQueue, WorkerRefusal } from "./workerQueue";

const PREVIEW_DEBOUNCE_MS = 300;

/** Edits whose refusal is shown under the expression field. */
const FIELD_EDITS: ReadonlySet<Edit["op"]> = new Set(["set_expression", "set_curve_component", "set_resolution"]);

/** Refusal codes that point at the edited field (C9.3). */
const FIELD_CODES: ReadonlySet<string> = new Set([
  "invalid_edit",
  "invalid_text",
  "unsupported_expression",
  "limit_exceeded",
  "untrusted_latex",
]);

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
  const [queue] = useState(
    () => new WorkerQueue({ report: (reason) => setError(errorMessage(reason)), resync: refreshState }),
  );
  const jobs = useRef<Handshake>(createHandshake());
  const armingExport = useRef(false);
  const notedReady = useRef(false);
  const epoch = useRef(0);
  const debounce = useRef<number | null>(null);
  const stateRef = useRef<EditorState | null>(null);
  const projectPath = useRef<string | null>(null);

  useLayoutEffect(() => {
    stateRef.current = state;
  }, [state]);

  const onWorkerLine = useEffectEvent((payload: unknown) => {
    try {
      const parsed = parseWorkerEvent(payload);
      if (parsed) {
        onWorkerEvent(parsed);
      }
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  });

  const onListening = useEffectEvent(() => {
    void queue.enqueue(refreshState);
    void queue.enqueue(requestReady);
  });

  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    let stopped = false;

    void (async () => {
      unlisten = await listen<unknown>("worker-event", (event) => onWorkerLine(event.payload));
      if (stopped) {
        unlisten();
        return;
      }
      onListening();
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

  /**
   * Asks the worker for `ready`. It is queued after the startup `get_state` and runs even when that load failed, so a
   * startup timeout recovers through the resync.
   */
  async function requestReady(): Promise<void> {
    try {
      await invokeWorker("worker_status");
    } catch (reason: unknown) {
      setStatus("Worker unavailable");
      throw reason;
    }
  }

  async function refreshState(): Promise<void> {
    applyState(parseEditorState(await invokeWorker("get_state")));
    setStatus((current) => (current === "Starting" ? "Ready" : current));
  }

  /** Applies a state reply. A state for another folder, such as a late Open reply, runs the Open reset (C9.2). */
  function applyState(next: EditorState): void {
    if (projectPath.current !== null && projectPath.current !== next.projectPath) {
      resetView();
    }
    projectPath.current = next.projectPath;
    setState(next);
  }

  /** Applies the reply of Open or Sample, which always runs the Open reset. */
  function applyOpened(next: EditorState): void {
    resetView();
    applyState(next);
    setError(null);
  }

  /** The Open reset (C9.2). */
  function resetView(): void {
    setSelection({ kind: "none" });
    setDrafts({});
    setPlayhead(0);
    setPlaying(false);
    setCodeOpen(false);
    setFieldError(null);
    jobs.current = { ...jobs.current, exportPath: null, exportNote: null };
    setExportPath(null);
    setExportNote(null);
  }

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

  function onWorkerEvent(message: WorkerEvent): void {
    if (message.event === "ready") {
      setReady(true);
    }
    const previewDone = message.event === "done" && message.jobId === jobs.current.previewJobId;
    const step = receive(jobs.current, message);
    publish(step.session);
    applyBeats(step.applied);
    if (previewDone) {
      void queue.enqueue(refreshState);
    }
  }

  function applyBeats(events: WorkerEvent[]): void {
    for (const event of events) {
      if (event.event === "beat_ready") {
        setState((current) => patchBeat(current, event.beatIndex, event.path));
      }
    }
  }

  function schedulePreview(): void {
    epoch.current += 1;
    const token = epoch.current;
    jobs.current = { ...jobs.current, epoch: token };
    if (debounce.current !== null) {
      window.clearTimeout(debounce.current);
    }
    debounce.current = window.setTimeout(() => {
      debounce.current = null;
      void queue.enqueue(() => runPreview(token));
    }, PREVIEW_DEBOUNCE_MS);
  }

  /** The preview task (C9.2). A later schedulePreview supersedes it until its id is recorded. */
  async function runPreview(token: number): Promise<void> {
    if (token !== epoch.current) {
      return;
    }
    const jobId = await invoke<string>("reserve_job_id", { prefix: "preview" });
    if (token !== epoch.current) {
      return;
    }
    const step = beginPreview(jobs.current, jobId, token);
    jobs.current = step.session;
    setStatus(step.session.status);
    setRendering(step.session.rendering);
    applyBeats(step.applied);
    try {
      await invokeWorker("render_preview", { jobId });
    } catch (reason: unknown) {
      if (reason instanceof WorkerFailure) {
        // A failed send ends the preview as if its terminal line were this error (C7.4).
        onWorkerEvent({ event: "error", jobId, code: reason.code, message: reason.message, details: reason.details });
      }
      throw reason;
    }
  }

  function commit(edit: Edit, preview: boolean): Promise<void> {
    return queue.enqueue(async () => {
      try {
        const next = parseEditorState(await invokeWorker("edit_document", { edit }));
        applyState(next);
        setError(null);
        setFieldError(null);
        if (next.focus?.beatId) {
          setSelection({ kind: "beat", id: next.focus.beatId });
        }
        if (preview) {
          schedulePreview();
        }
      } catch (reason: unknown) {
        if (reason instanceof WorkerRefusal && FIELD_CODES.has(reason.code) && FIELD_EDITS.has(edit.op)) {
          setFieldError(reason.message);
        }
        throw reason;
      }
    });
  }

  function undo(): Promise<void> {
    return runHistory("undo_document");
  }

  function redo(): Promise<void> {
    return runHistory("redo_document");
  }

  function runHistory(command: "undo_document" | "redo_document"): Promise<void> {
    return queue.enqueue(async () => {
      applyState(parseEditorState(await invokeWorker(command)));
      setError(null);
      setDrafts({});
    });
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
    void queue.enqueue(() => runExport(edit));
  }

  /** The Export task (C9.2): reserve an id, save the profile, then send the export unless Cancel came first. */
  async function runExport(edit: Edit): Promise<void> {
    try {
      const jobId = await invoke<string>("reserve_job_id", { prefix: "export" });
      const begun = beginExport(jobs.current, jobId);
      publish(begun.session);
      applyBeats(begun.applied);
      if (!begun.session.exporting) {
        armingExport.current = false;
        return;
      }
      applyState(parseEditorState(await invokeWorker("edit_document", { edit })));
      if (jobs.current.pendingCancel) {
        stopExport("Cancelled");
        return;
      }
      await invokeWorker("export_movie", { jobId });
      const sent = markExportSent(jobs.current);
      jobs.current = sent.session;
      armingExport.current = false;
      if (sent.cancelJobId) {
        await invokeWorker("cancel_render", { jobId: sent.cancelJobId });
      }
    } catch (reason: unknown) {
      stopExport("Ready");
      throw reason;
    }
  }

  function stopExport(status: string): void {
    armingExport.current = false;
    publish({
      ...jobs.current,
      exportJobId: null,
      exportCommandSent: false,
      pendingCancel: false,
      exporting: false,
      exportLabel: "Export",
      status,
    });
  }

  function onCancelExport(): void {
    if (armingExport.current && jobs.current.exportJobId === null) {
      jobs.current = armExportCancel(jobs.current);
      return;
    }
    const step = requestCancel(jobs.current);
    jobs.current = step.session;
    const jobId = step.cancelJobId;
    if (jobId !== null) {
      void queue.enqueue(() => invokeWorker("cancel_render", { jobId }));
    }
  }

  function onClearCache(): Promise<void> {
    return queue.enqueue(async () => {
      const next = parseEditorState(await invokeWorker("clear_cache"));
      applyState(next);
      setError(next.warning?.message ?? null);
      setExportPath(null);
    });
  }

  function onNotices(): void {
    setCodeOpen(false);
    setNoticesOpen(true);
  }

  function onOpenSample(): Promise<void> {
    return queue.enqueue(async () => {
      applyOpened(parseEditorState(await invokeWorker("open_sample")));
      setNoticesOpen(false);
    });
  }

  function onViewCode(): Promise<void> {
    setNoticesOpen(false);
    setCodeOpen(true);
    return queue.enqueue(async () => {
      const read = fieldsOf(await invokeWorker("view_code"), "code");
      setCodeText(read("source", asString));
    });
  }

  function onExportCode(): Promise<void> {
    return queue.enqueue(async () => {
      const reply = await invokeWorker("export_code_dialog");
      if (reply === null) {
        return;
      }
      const read = fieldsOf(reply, "export_code_dialog");
      setExportNote(`Code exported to ${read("path", asString)}`);
      setError(null);
    });
  }

  function onOpen(): Promise<void> {
    return queue.enqueue(async () => {
      const reply = await invokeWorker("open_project_dialog");
      if (reply !== null) {
        applyOpened(parseEditorState(reply));
      }
    });
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
