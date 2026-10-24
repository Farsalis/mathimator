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
  type Handshake,
  type JobFailure,
} from "./jobSession";
import { asString, fieldsOf } from "./parse";
import { patchBeat } from "./playback";
import {
  parseAppStatus,
  parseEditorState,
  parseWorkerEvent,
  type AppStatus,
  type Edit,
  type EditorState,
  type Notice,
  type ProjectDocument,
  type TexStatus,
  type WorkerEvent,
} from "./types";
import {
  errorMessage,
  failureOf,
  invokeWorker,
  WorkerFailure,
  WorkerQueue,
  WorkerRefusal,
  type Failure,
  type Task,
} from "./workerQueue";

export const PREVIEW_DEBOUNCE_MS = 300;

const EXPORT_RUNNING = "An export is already running.";
const RESTART_NOTE = "The worker restarted. The document was reloaded from disk and undo history was cleared.";
const WORKER_UNAVAILABLE = "Worker unavailable";
const TEX_FAILED = "Formulas and axis labels cannot render on this PC. Reinstall Mathimator.";

/** Refusal codes that point at the edited field (C9.3). */
const FIELD_CODES: ReadonlySet<string> = new Set([
  "invalid_edit",
  "invalid_text",
  "unsupported_expression",
  "limit_exceeded",
  "untrusted_latex",
]);

export type Selection = { kind: "none" } | { kind: "beat"; id: string } | { kind: "object"; id: string };

/** A refused edit's message, shown under the field it names on the object it edited (C9.3, S59). */
export interface FieldError {
  objectId: string;
  /** The edit field the refusal names, for example `expression`. */
  field: string;
  /** The refused edit's op and axis, which tell apart the axes inputs whose edits all name `value`. */
  op: Edit["op"];
  axis: string | null;
  message: string;
}

type OpenCommand = "open_project_dialog" | "open_sample" | "new_project";

export function useEditor() {
  const [state, setState] = useState<EditorState | null>(null);
  /** Why the worker is unavailable (C9.5), or null. */
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [tex, setTex] = useState<TexStatus>({ state: "checking", message: null });
  const [notices, setNotices] = useState<Notice[]>([]);
  const [error, setError] = useState<Failure | null>(null);
  const [fieldError, setFieldError] = useState<FieldError | null>(null);
  /** The message of the open project_changed prompt (C9.3), or null. */
  const [changedPrompt, setChangedPrompt] = useState<string | null>(null);
  const [handshake, setHandshake] = useState(createHandshake);
  const [selection, setSelection] = useState<Selection>({ kind: "none" });
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [codeOpen, setCodeOpen] = useState(false);
  const [noticesOpen, setNoticesOpen] = useState(false);
  const [presetOpen, setPresetOpen] = useState(false);
  const [queue] = useState(
    () =>
      new WorkerQueue({
        report: showFailure,
        resync: refreshState,
        changed: (refusal, retry) => joinPrompt(refusal.message, retry),
      }),
  );
  const jobs = useRef(handshake);
  /** The last applied state reply. Queued tasks read it before React has rendered it. */
  const applied = useRef<EditorState | null>(null);
  /** The worker exited or was restarted, so the next get_state is the C8.6 resync. */
  const restarted = useRef(false);
  const notedReady = useRef(false);
  /** Bumped by every schedulePreview, so a later one supersedes a preview task that has not recorded its id. */
  const epoch = useRef(0);
  const debounce = useRef<number | null>(null);
  /** The id of every notice shown so far: each id is shown once (C9.5). */
  const shownNotices = useRef(new Set<number>());
  /** What Overwrite runs again for the refusals joined to the open prompt, or null while none is open (C9.3). */
  const joined = useRef<Task[] | null>(null);
  /** The profile and `force` of the current export, so a refused export is retried the same way (C9.3). */
  const exportOrder = useRef<{ profile: ExportProfile; force: boolean } | null>(null);

  const ready = state !== null && unavailable === null;

  const onWorkerLine = useEffectEvent((payload: unknown) => {
    try {
      const parsed = parseWorkerEvent(payload);
      if (parsed) {
        onWorkerEvent(parsed);
      }
    } catch (reason: unknown) {
      setError(failureOf(reason));
    }
  });

  const onListening = useEffectEvent(() => {
    void loadStatus();
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
    if (ready && !notedReady.current) {
      notedReady.current = true;
      void invoke("note_shell_ready");
    }
  }, [ready]);

  /** Startup (C9.5): `app_status` answers once the shell's handshake is done. */
  async function loadStatus(): Promise<void> {
    try {
      applyStatus(parseAppStatus(await invoke("app_status")));
    } catch (reason: unknown) {
      setUnavailable(errorMessage(reason));
    }
  }

  /** Retry in the "Worker unavailable" view. A worker that starts is loaded as after a restart (C8.6). */
  async function onRetry(): Promise<void> {
    try {
      const status = parseAppStatus(await invoke("restart_worker"));
      restarted.current = true;
      applyStatus(status);
    } catch (reason: unknown) {
      setUnavailable(errorMessage(reason));
    }
  }

  /**
   * Shows the notices and TeX state of an AppStatus, then loads the state of a ready worker. A `tex_status` event can
   * overtake the reply (C9.2), and the check never goes back to `checking`, so a reply still checking keeps the result.
   */
  function applyStatus(status: AppStatus): void {
    addNotices(status.notices);
    setTex((current) => (status.tex.state === "checking" ? current : status.tex));
    if (status.worker === "unavailable") {
      setUnavailable(status.reason ?? "");
      return;
    }
    setUnavailable(null);
    void queue.enqueue(refreshState);
  }

  /** Shows the notices whose ids were never shown, so a StrictMode double mount shows each once (C9.5). */
  function addNotices(added: readonly Notice[]): void {
    const fresh = added.filter((notice) => !shownNotices.current.has(notice.id));
    for (const notice of fresh) {
      shownNotices.current.add(notice.id);
    }
    if (fresh.length > 0) {
      setNotices((current) => [...current, ...fresh]);
    }
  }

  function onDismissNotice(id: number): void {
    setNotices((current) => current.filter((notice) => notice.id !== id));
    void invoke("dismiss_notice", { id });
  }

  /** Shows why a task failed: `worker_unavailable` opens the unavailable view (C9.5), anything else the banner. */
  function showFailure(reason: unknown): void {
    if (reason instanceof WorkerFailure && reason.code === "worker_unavailable") {
      setUnavailable(reason.message);
    } else {
      setError(failureOf(reason));
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
      reconcile(next.document);
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

  /** Applies the reply of Open, Sample or New: the Open reset, then a preview (C7.5). */
  function applyOpened(next: EditorState): void {
    resetView();
    applyState(next);
    setError(null);
    setNoticesOpen(false);
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

  /** After the document was replaced from disk: clear the drafts and field errors, and drop a selection that is gone. */
  function reconcile(document: ProjectDocument): void {
    setDrafts({});
    setFieldError(null);
    setSelection((current) => (selectionIn(current, document) ? current : { kind: "none" }));
  }

  function publish(next: Handshake): void {
    jobs.current = next;
    setHandshake(next);
  }

  function onWorkerEvent(message: WorkerEvent): void {
    if (message.event === "notice") {
      addNotices([{ id: message.id, message: message.message }]);
    } else if (message.event === "tex_status") {
      setTex({ state: message.state, message: message.message });
    }
    const step = receive(jobs.current, message);
    publish(step.session);
    if (step.failure?.code === "project_changed") {
      queue.pause();
      joinPrompt(step.failure.message, jobRetry(step.failure.job));
    } else if (step.failure) {
      setError(step.failure);
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

  /**
   * A `project_changed` refusal, with the queue paused. It joins the open prompt or opens one, and Overwrite runs
   * `retry` (C9.3).
   */
  function joinPrompt(message: string, retry: Task): void {
    if (joined.current) {
      joined.current.push(retry);
      return;
    }
    joined.current = [retry];
    setChangedPrompt(message);
  }

  /** Closes the prompt and returns what Overwrite would run again. */
  function closePrompt(): Task[] {
    const retries = joined.current ?? [];
    joined.current = null;
    setChangedPrompt(null);
    return retries;
  }

  /** Reload: read project.json again, which discards undo history and the joined refusals, then preview (C9.3, Q7). */
  function onReload(): void {
    closePrompt();
    queue.resume([
      async () => {
        const next = parseEditorState(await invokeWorker("reload_project"));
        applyState(next);
        publish(markStale(jobs.current));
        reconcile(next.document);
        setError(null);
        schedulePreview();
      },
    ]);
  }

  /** Overwrite: save this document over project.json, then retry each joined refusal once, in order (C9.3). */
  function onOverwrite(): void {
    const retries = closePrompt();
    let saved = false;
    queue.resume([
      async () => {
        applyState(parseEditorState(await invokeWorker("overwrite_project")));
        setError(null);
        saved = true;
      },
      ...retries.map((retry) => async () => {
        if (saved) {
          await retry();
        }
      }),
    ]);
  }

  /** Cancel: write nothing, drop the joined refusals and go on with the queue (C13.2 E2). */
  function onCancelPrompt(): void {
    closePrompt();
    queue.resume();
  }

  /** What Overwrite runs again for a job refused with `project_changed`: a new job of the same kind (C9.3). */
  function jobRetry(job: JobFailure["job"]): Task {
    const order = exportOrder.current;
    return job === "export" && order !== null ? exportRetry(order.profile, order.force) : previewNow;
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

  /** A preview task that supersedes every scheduled one: the retry of a refused preview replaces it (C7.5). */
  function previewNow(): Promise<void> {
    epoch.current += 1;
    return runPreview(epoch.current);
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
        if (
          reason instanceof WorkerRefusal &&
          FIELD_CODES.has(reason.code) &&
          reason.field !== undefined &&
          "objectId" in edit
        ) {
          setFieldError({
            objectId: edit.objectId,
            field: reason.field,
            op: edit.op,
            axis: "axis" in edit ? edit.axis : null,
            message: reason.message,
          });
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
      setFieldError(null);
      setDrafts({});
    });
  }

  /** Whether an export runs. If one does, the note says so. */
  function exportRunning(): boolean {
    if (jobs.current.exporting) {
      publish(withNote(jobs.current, EXPORT_RUNNING));
    }
    return jobs.current.exporting;
  }

  function onExport(): void {
    if (exportRunning()) {
      return;
    }
    publish(withNote(jobs.current, null));
    setPresetOpen(true);
  }

  /** The Export click: "Rendering export" at once, then the Export task. Re-export passes `force` (D56). */
  function onStartExport(profile: ExportProfile, force: boolean): void {
    if (exportRunning()) {
      return;
    }
    publish(requestExport(jobs.current));
    setError(null);
    setPresetOpen(false);
    void queue.enqueue(() => runExport(profile, force), exportRetry(profile, force));
  }

  /** The retry of a refused export: a new Export task with a new id, the same profile and the same `force` (C9.3). */
  function exportRetry(profile: ExportProfile, force: boolean): Task {
    return async () => {
      if (exportRunning()) {
        return;
      }
      publish(requestExport(jobs.current));
      await runExport(profile, force);
    };
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
      exportOrder.current = { profile, force };
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
      setError(next.warning ?? null);
      schedulePreview();
    });
  }

  function onNotices(): void {
    setCodeOpen(false);
    setNoticesOpen(true);
  }

  /** View code shows the applied state's source, so it follows every change (D25). */
  function onViewCode(): void {
    setNoticesOpen(false);
    setCodeOpen(true);
  }

  /**
   * Open, Sample and New. The shell first offers to save a touched temp project (C14 Q2); a cancel there, or in the
   * folder picker, returns null and changes nothing.
   */
  function openProject(command: OpenCommand): Promise<void> {
    return queue.enqueue(async () => {
      const reply = await invokeWorker(command);
      if (reply !== null) {
        applyOpened(parseEditorState(reply));
      }
    });
  }

  /** Save As (D29). The reply carries the new folder, so applying it runs the Open reset (C9.2). */
  function onSaveAs(): Promise<void> {
    return queue.enqueue(async () => {
      const reply = await invokeWorker("save_project_as_dialog");
      if (reply !== null) {
        applyState(parseEditorState(reply));
        setError(null);
      }
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
    unavailable,
    texWarning: tex.state === "failed" ? (tex.message ?? TEX_FAILED) : null,
    notices,
    error,
    fieldError,
    changedPrompt,
    status: unavailable === null ? handshake.status : WORKER_UNAVAILABLE,
    selection,
    setSelection,
    drafts,
    playhead,
    setPlayhead,
    playing,
    setPlaying,
    codeOpen,
    setCodeOpen,
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
    onRetry,
    onDismissNotice,
    onDismissError: () => setError(null),
    onReload,
    onOverwrite,
    onCancelPrompt,
    onExport,
    onStartExport,
    onCancelExport,
    onClearCache,
    onNotices,
    onViewCode,
    onExportCode,
    onOpen: () => openProject("open_project_dialog"),
    onOpenSample: () => openProject("open_sample"),
    onNew: () => openProject("new_project"),
    onSaveAs,
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
