import type { WorkerEvent } from "./types";

/**
 * The job handshake (C1): the preview and export the UI waits for, the status line (C7.4), the Export button and the
 * note under the status line. It changes only through the pure functions below.
 */
export interface Handshake {
  /** The current preview job. Its id is recorded before render_preview is sent, so no event can come first (C7.1). */
  previewJobId: string | null;
  /** A state change overtook the current preview, so its beat_ready lines are ignored (C9.2, F135). */
  previewStale: boolean;
  /** The current export job, recorded before export_movie is sent. */
  exportJobId: string | null;
  /** From the Export click until the export's terminal line, or until the Export task stops (C7.6). */
  exporting: boolean;
  /** Cancel export was pressed for the current export (C9.2). */
  cancelRequested: boolean;
  /** The current export pre-empted a preview, so a preview follows the export (C7.5). */
  preempted: boolean;
  status: string;
  /** The note under the status line. */
  note: string | null;
  /** The file the last export published. */
  exportPath: string | null;
}

/** The terminal `error` or `rejected` line of a job, shown as a banner or the project_changed prompt (C9.3). */
export interface JobFailure {
  /** The job that ended, so a `project_changed` refusal can be retried as the same kind of job. */
  job: "preview" | "export";
  code: string;
  message: string;
  details?: string;
}

/**
 * What the UI queues after a worker event:
 * - `refresh`: the current preview is done, so load the state with get_state (C9.2);
 * - `preview`: an export that pre-empted a preview ended, so schedule a preview (C7.5);
 * - `resync`: the worker exited, so resync once (C8.6).
 */
export type FollowUp = "refresh" | "preview" | "resync" | null;

export interface HandshakeStep {
  session: Handshake;
  /** The failure of the event's job. It belongs to this step only, so it is never shown twice (F109). */
  failure: JobFailure | null;
  /** A beat of the current, non-stale preview, to patch into the preview index (C9.2). */
  beat: { beatIndex: number; path: string } | null;
  next: FollowUp;
}

/** How a stopped Export task leaves the status line. */
export type ExportStop = "Ready" | "Cancelled";

const READY = "Ready";
const CANCELLED = "Cancelled";
const RENDERING_PREVIEW = "Rendering preview";
const RENDERING_EXPORT = "Rendering export";
const WAITING = "Waiting for the previous render to stop";
const OLDER_EXPORT = "This file is from an older export.";

export function createHandshake(): Handshake {
  return {
    previewJobId: null,
    previewStale: false,
    exportJobId: null,
    exporting: false,
    cancelRequested: false,
    preempted: false,
    status: "Starting",
    note: null,
    exportPath: null,
  };
}

/** The first state was applied. */
export function loaded(session: Handshake): Handshake {
  return session.status === "Starting" ? { ...session, status: READY } : session;
}

export function withNote(session: Handshake, note: string | null): Handshake {
  return { ...session, note };
}

/** The handshake's part of the Open reset (C9.2): a stale preview, and no export path, note or pre-empted preview. */
export function openReset(session: Handshake): Handshake {
  return { ...markStale(session), exportPath: null, note: null, preempted: false };
}

/**
 * The handshake's part of Save As from the folder `from` to `to` (D29, O9), which keeps the note. The worker copied the
 * exports with the project, so a shown export in `from` is shown as its copy in `to`. The state of another folder still
 * overtakes the current preview (C9.2).
 */
export function savedAs(session: Handshake, from: string, to: string): Handshake {
  const path = session.exportPath;
  return { ...markStale(session), exportPath: path === null ? null : movedPath(path, from, to) };
}

/** An applied edit that changed the document, or an undo, redo or reload, overtook the current preview (C9.2). */
export function markStale(session: Handshake): Handshake {
  return session.previewJobId === null || session.previewStale ? session : { ...session, previewStale: true };
}

/** Records the preview task's job id. A new preview is never stale. */
export function beginPreview(session: Handshake, jobId: string): Handshake {
  return {
    ...session,
    previewJobId: jobId,
    previewStale: false,
    status: session.exporting ? session.status : RENDERING_PREVIEW,
  };
}

/** Ends a preview whose render_preview send failed, as if its terminal line were an error (C7.4). */
export function abandonPreview(session: Handshake, jobId: string): Handshake {
  return session.previewJobId === jobId ? previewEnded(session) : session;
}

/** The Export click: "Rendering export" before anything is queued (C7.4). */
export function requestExport(session: Handshake): Handshake {
  return {
    ...session,
    exporting: true,
    cancelRequested: false,
    preempted: false,
    status: RENDERING_EXPORT,
    note: null,
  };
}

/** Records the Export task's job id. A preview that is current now is pre-empted by the export (C7.5). */
export function beginExport(session: Handshake, jobId: string): Handshake {
  return {
    ...session,
    exportJobId: jobId,
    exporting: true,
    preempted: session.preempted || session.previewJobId !== null,
    status: RENDERING_EXPORT,
  };
}

/** Cancel export: the export's later `error cancelled` reads "Cancelled" (C9.2). */
export function requestCancel(session: Handshake): Handshake {
  return session.exporting ? { ...session, cancelRequested: true } : session;
}

/** The Export task stopped before export_movie was sent, or the send failed (C7.4). */
export function abortExport(session: Handshake, status: ExportStop): Handshake {
  return exportEnded(session, status);
}

/** Applies a `worker-event` line. Job events count only for the current preview or export id (C7.1, C9.4). */
export function receive(session: Handshake, message: WorkerEvent): HandshakeStep {
  switch (message.event) {
    case "worker_exited":
      // Every job ends and nothing reads "Cancelled" (C8.6, D21).
      return step({ ...exportEnded(session, READY), previewJobId: null }, { next: "resync" });
    case "notice":
    case "tex_status":
      return step(session);
    default:
      if (message.jobId === null) {
        return step(session);
      }
      if (message.jobId === session.previewJobId) {
        return previewEvent(session, message);
      }
      if (message.jobId === session.exportJobId) {
        return exportEvent(session, message);
      }
      return step(session);
  }
}

type JobEvent = Exclude<WorkerEvent, { event: "worker_exited" | "notice" | "tex_status" }>;

function previewEvent(session: Handshake, message: JobEvent): HandshakeStep {
  switch (message.event) {
    case "queued":
      return step(session.exporting ? session : { ...session, status: WAITING });
    case "started":
      return step(session.exporting ? session : { ...session, status: RENDERING_PREVIEW });
    case "beat_ready": {
      if (session.previewStale) {
        return step(session);
      }
      const status = `Rendering preview — beat ${message.beatIndex + 1}`;
      return step(session.exporting ? session : { ...session, status }, {
        beat: { beatIndex: message.beatIndex, path: message.path },
      });
    }
    case "done":
      return step(previewEnded(session), { next: "refresh" });
    case "error":
    case "rejected":
      if (isCancel(message)) {
        // Previews have no Cancel control, so a preview's cancel never reads "Cancelled" (C7.3, F041).
        return step({ ...previewEnded(session), preempted: session.preempted || session.exporting });
      }
      return step(previewEnded(session), { failure: failureOf("preview", message) });
  }
}

function exportEvent(session: Handshake, message: JobEvent): HandshakeStep {
  const next = session.preempted && session.previewJobId === null ? "preview" : null;
  switch (message.event) {
    case "queued":
      return step({ ...session, status: WAITING });
    case "started":
      return step({ ...session, status: RENDERING_EXPORT });
    case "beat_ready":
      // Exports send none (D17), and an export's partial never goes into the preview index (F032).
      return step(session);
    case "done": {
      const note = message.current === false ? OLDER_EXPORT : null;
      return step({ ...exportEnded(session, READY), exportPath: message.path, note }, { next });
    }
    case "error":
    case "rejected":
      if (isCancel(message)) {
        return step(exportEnded(session, session.cancelRequested ? CANCELLED : READY), { next });
      }
      return step(exportEnded(session, READY), { failure: failureOf("export", message), next });
  }
}

function previewEnded(session: Handshake): Handshake {
  return { ...session, previewJobId: null, status: session.exporting ? session.status : READY };
}

function exportEnded(session: Handshake, status: string): Handshake {
  return { ...session, exportJobId: null, exporting: false, cancelRequested: false, preempted: false, status };
}

/** `path` in the folder `to` when it lies in the folder `from`, otherwise `path` as it is. */
function movedPath(path: string, from: string, to: string): string {
  const inside = path.startsWith(from) && /[\\/]/.test(path.charAt(from.length));
  return inside ? to + path.slice(from.length) : path;
}

function isCancel(message: { event: "error" | "rejected"; code: string }): boolean {
  return message.event === "error" && message.code === "cancelled";
}

function failureOf(job: JobFailure["job"], message: Extract<JobEvent, { event: "error" | "rejected" }>): JobFailure {
  return {
    job,
    code: message.code,
    message: message.message,
    details: message.event === "error" ? message.details : undefined,
  };
}

function step(session: Handshake, effects: Partial<Omit<HandshakeStep, "session">> = {}): HandshakeStep {
  return { session, failure: null, beat: null, next: null, ...effects };
}
