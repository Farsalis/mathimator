import { useEffect, useEffectEvent, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { derivedStarts } from "./format";
import type { ExportProfile } from "./exportProfile";
import {
  abandonPreview,
  abortExport,
  beginExport,
  beginPreview,
  clearExportResult,
  createHandshake,
  loaded,
  markStale,
  openReset,
  receive,
  requestCancel,
  requestExport,
  withNote,
  workerUnavailable,
  type Handshake,
} from "./jobSession";
import { asString, fieldsOf } from "./parse";
import { patchBeat } from "./playback";
import {
  parseEditorState,
  parseWorkerEvent,
  type Edit,
  type EditorState,
  type ProjectDocument,
  type WorkerEvent,
} from "./types";
import { errorMessage, invokeWorker, WorkerQueue, WorkerRefusal } from "./workerQueue";

export const PREVIEW_DEBOUNCE_MS = 300;

const EXPORT_RUNNING = "An export is already running.";
const RESTART_NOTE = "The worker restarted. The document was reloaded from disk and undo history was cleared.";

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
  const [handshake, setHandshake] = useState(createHandshake);
  const [selection, setSelection] = useState<Selection>({ kind: "none" });
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [codeOpen, setCodeOpen] = useState(false);
  const [codeText, setCodeText] = useState("");
  const [noticesOpen, setNoticesOpen] = useState(false);
  const [presetOpen, setPresetOpen] = useState(false);
  const [queue] = useState(
    () => new WorkerQueue({ report: (reason) => setError(errorMessage(reason)), resync: refreshState }),
  );
  const jobs = useRef(handshake);
  /** The last applied state reply. Queued tasks read it before React has rendered it. */
  const applied = useRef<EditorState | null>(null);
  /** The worker exited, so the next get_state is the C8.6 resync. */
  const restarted = useRef(false);
  const notedReady = useRef(false);
  /** Bumped by every schedulePreview, so a later one supersedes a preview task that has not recorded its id. */
  const epoch = useRef(0);
  const debounce = useRef<number | null>(null);

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
      publish(workerUnavailable(jobs.current));
      throw reason;
    }
  }

  /**
   * Loads the worker's state: at startup, after a preview, and as the queue's single-flight resync. The first load
   * after the worker exited also clears the drafts, drops a selection that is gone and shows the restart note. On
   * another folder the Open reset does that instead, and no note is shown (C8.6). No preview follows (Q5).
   */
  async function refreshState(): Promise<void> {
    const restart = restarted.current;
    restarted.current = false;
    const next = parseEditorState(await invokeWorker("get_state"));
    const sameProject = applied.current?.projectPath === next.projectPath;
    applyState(next);
    publish(loaded(jobs.current));
    if (restart && sameProject) {
      setDrafts({});
      setSelection((current) => (selectionIn(current, next.document) ? current : { kind: "none" }));
      publish(withNote(jobs.current, RESTART_NOTE));
    }
  }

  /** Applies a state reply. A state for another folder, such as a late Open reply, runs the Open reset (C9.2). */
  function applyState(next: EditorState): void {
    if (applied.current !== null && applied.current.projectPath !== next.projectPath) {
      resetView();
    }
    applied.current = next;
    setState(next);
  }

  /** Applies an edit reply. An edit that changed the document overtakes the current preview (C9.2). */
  function applyEdit(next: EditorState): void {
    applyState(next);
    if (next.changed !== false) {
      publish(markStale(jobs.current));
    }
  }

  /** Applies the reply of Open or Sample: the Open reset, then a preview (C7.5). */
  function applyOpened(next: EditorState): void {
    resetView();
    applyState(next);
    setError(null);
    schedulePreview();
  }

  /** The Open reset (C9.2). */
  function resetView(): void {
    setSelection({ kind: "none" });
    setDrafts({});
    setPlayhead(0);
    setPlaying(false);
    setCodeOpen(false);
    setFieldError(null);
    publish(openReset(jobs.current));
  }

  function publish(next: Handshake): void {
    jobs.current = next;
    setHandshake(next);
  }

  function onWorkerEvent(message: WorkerEvent): void {
    if (message.event === "ready") {
      setReady(true);
    }
    const step = receive(jobs.current, message);
    publish(step.session);
    if (step.failure) {
      setError(step.failure.message);
    }
    const beat = step.beat;
    if (beat) {
      setState((current) => patchBeat(current, beat.beatIndex, beat.path));
    }
    switch (step.next) {
      case "refresh":
        void queue.enqueue(refreshState);
        break;
      case "preview":
        schedulePreview();
        break;
      case "resync":
        restarted.current = true;
        queue.resync();
        break;
      case null:
        break;
    }
  }

  /** Schedules a preview of the applied state after the debounce. A scene with no beats gets none (C7.5). */
  function schedulePreview(): void {
    epoch.current += 1;
    const token = epoch.current;
    if (debounce.current !== null) {
      window.clearTimeout(debounce.current);
      debounce.current = null;
    }
    if ((applied.current?.document.scene.beats ?? []).length === 0) {
      return;
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
    publish(beginPreview(jobs.current, jobId));
    try {
      await invokeWorker("render_preview", { jobId });
    } catch (reason: unknown) {
      // A failed send ends the preview as if its terminal line were this error (C7.4); the queue shows the banner.
      publish(abandonPreview(jobs.current, jobId));
      throw reason;
    }
  }

  function commit(edit: Edit, preview: boolean): Promise<void> {
    return queue.enqueue(async () => {
      try {
        const next = parseEditorState(await invokeWorker("edit_document", { edit }));
        applyEdit(next);
        setError(null);
        setFieldError(null);
        if (next.focus?.beatId) {
          setSelection({ kind: "beat", id: next.focus.beatId });
        }
        // An edit that changed nothing gets no preview (D08).
        if (preview && next.changed !== false) {
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

  /** Undo and redo overtake the current preview and start none (spec §2). */
  function runHistory(command: "undo_document" | "redo_document"): Promise<void> {
    return queue.enqueue(async () => {
      applyState(parseEditorState(await invokeWorker(command)));
      publish(markStale(jobs.current));
      setError(null);
      setDrafts({});
    });
  }

  function onExport(): void {
    if (jobs.current.exporting) {
      publish(withNote(jobs.current, EXPORT_RUNNING));
      return;
    }
    publish(withNote(jobs.current, null));
    setPresetOpen(true);
  }

  /** The Export click: "Rendering export" at once, then the Export task. Re-export passes `force` (D56). */
  function onStartExport(profile: ExportProfile, force: boolean): void {
    if (jobs.current.exporting) {
      publish(withNote(jobs.current, EXPORT_RUNNING));
      return;
    }
    publish(requestExport(jobs.current));
    setError(null);
    setPresetOpen(false);
    void queue.enqueue(() => runExport(profile, force));
  }

  /**
   * The Export task (C9.2): record the id, save the profile (a no-op when it is unchanged, F168), then send the export.
   * Cancel export pressed before the send stops the task with "Cancelled"; a refused or failed step stops it with
   * "Ready". After worker_exited has ended the export (C8.6), the task stops without sending anything more.
   */
  async function runExport(profile: ExportProfile, force: boolean): Promise<void> {
    try {
      const jobId = await invoke<string>("reserve_job_id", { prefix: "export" });
      if (!jobs.current.exporting) {
        return;
      }
      publish(beginExport(jobs.current, jobId));
      if (!jobs.current.cancelRequested) {
        const edit: Edit = { op: "set_export_profile", ...profile };
        applyEdit(parseEditorState(await invokeWorker("edit_document", { edit })));
      }
      if (jobs.current.exportJobId !== jobId) {
        return;
      }
      if (jobs.current.cancelRequested) {
        publish(abortExport(jobs.current, "Cancelled"));
        return;
      }
      await invokeWorker("export_movie", { jobId, force });
    } catch (reason: unknown) {
      publish(abortExport(jobs.current, "Ready"));
      throw reason;
    }
  }

  /**
   * Cancel export: marks the cancel as the user's and queues the Cancel task. The queue runs it after the Export task,
   * so an export id that is still current has been sent; otherwise the Export task has already stopped (C9.2).
   */
  function onCancelExport(): void {
    if (!jobs.current.exporting || jobs.current.cancelRequested) {
      return;
    }
    publish(requestCancel(jobs.current));
    void queue.enqueue(async () => {
      const jobId = jobs.current.exportJobId;
      if (jobId !== null) {
        await invokeWorker("cancel_render", { jobId });
      }
    });
  }

  function onClearCache(): Promise<void> {
    return queue.enqueue(async () => {
      const next = parseEditorState(await invokeWorker("clear_cache"));
      applyState(next);
      publish(clearExportResult(jobs.current));
      setError(next.warning?.message ?? null);
      schedulePreview();
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
      publish(withNote(jobs.current, `Code exported to ${read("path", asString)}`));
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
    const beat = applied.current?.document.scene.beats.find((item) => item.id === beatId);
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
    status: handshake.status,
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
    exporting: handshake.exporting,
    note: handshake.note,
    exportPath: handshake.exportPath,
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

/** Whether `document` still holds the selected beat or object. */
function selectionIn(selection: Selection, document: ProjectDocument): boolean {
  switch (selection.kind) {
    case "none":
      return true;
    case "beat":
      return document.scene.beats.some((beat) => beat.id === selection.id);
    case "object":
      return document.scene.objects.some((object) => object.id === selection.id);
  }
}
