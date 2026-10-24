import { invoke, type InvokeArgs } from "@tauri-apps/api/core";
import { isRecord } from "./parse";
import { parseWorkerEvent } from "./types";

/** The worker-bound shell commands. Each one goes through `invokeWorker` inside a queued task (C9.2). */
export type WorkerCommand =
  | "get_state"
  | "worker_status"
  | "edit_document"
  | "undo_document"
  | "redo_document"
  | "clear_cache"
  | "open_sample"
  | "open_project_dialog"
  | "view_code"
  | "export_code_dialog"
  | "render_preview"
  | "export_movie"
  | "cancel_render";

const TIMEOUT_MESSAGE = "The worker did not answer in time.";

/** A `rejected` reply: the worker refused the request before changing anything, and `message` is for the user. */
export class WorkerRefusal extends Error {
  name = "WorkerRefusal";

  constructor(
    readonly code: string,
    message: string,
    readonly field?: string,
  ) {
    super(message);
  }
}

/**
 * An `error` reply, or a shell failure: `worker_unavailable`, `worker_exited`, `timeout`, `io` or `bad_argument`
 * (D45). A rejection that is not a shell error, such as Tauri's own text for an unknown command, has the code
 * `invoke_failed`.
 */
export class WorkerFailure extends Error {
  name = "WorkerFailure";

  constructor(
    readonly code: string,
    message: string,
    readonly details?: string,
  ) {
    super(message);
  }
}

/** The text to show for a failure: the message of an Error or of a shell error object, or the value itself. */
export function errorMessage(reason: unknown): string {
  if (reason instanceof Error) {
    return reason.message;
  }
  if (isRecord(reason) && typeof reason.message === "string") {
    return reason.message;
  }
  return String(reason);
}

/**
 * Invokes a worker-bound command and returns its success value; a cancelled dialog returns null. Throws
 * WorkerRefusal for a `rejected` reply and WorkerFailure for an `error` reply or a shell failure (D46).
 */
export async function invokeWorker(command: WorkerCommand, args?: InvokeArgs): Promise<unknown> {
  const reply = await invoke<unknown>(command, args).catch((reason: unknown) => {
    throw shellFailure(reason);
  });
  const event = parseWorkerEvent(reply);
  if (event?.event === "rejected") {
    throw new WorkerRefusal(event.code, event.message, event.field);
  }
  if (event?.event === "error") {
    throw new WorkerFailure(event.code, event.message, event.details);
  }
  return isRecord(reply) && reply.cancelled === true ? null : reply;
}

function shellFailure(reason: unknown): WorkerFailure {
  if (isRecord(reason) && typeof reason.code === "string") {
    return new WorkerFailure(reason.code, reason.code === "timeout" ? TIMEOUT_MESSAGE : errorMessage(reason));
  }
  return new WorkerFailure("invoke_failed", errorMessage(reason));
}

export interface QueueHooks {
  /** Shows why a task failed. */
  report: (reason: unknown) => void;
  /** Reloads the worker's state with `get_state` and applies it. */
  resync: () => Promise<void>;
}

type Task = () => Promise<unknown>;

/**
 * The one ordered queue of worker tasks (D20, C9.2). Tasks run one at a time in call order, so at most one worker
 * request is outstanding and each task applies its results before the next one starts. A failed task is reported
 * and never blocks later ones. A timeout also queues the `get_state` resync, at most one at a time (F164).
 */
export class WorkerQueue {
  private tail: Promise<void> = Promise.resolve();
  private resyncQueued = false;

  constructor(private readonly hooks: QueueHooks) {}

  /** Queues `task`. The returned promise settles once the task has finished, and never rejects. */
  enqueue(task: Task): Promise<void> {
    return this.schedule(task, (reason) => {
      this.hooks.report(reason);
      if (reason instanceof WorkerFailure && reason.code === "timeout") {
        this.resync();
      }
    });
  }

  /** Queues the resync unless one is already queued or running. A resync that fails is reported, not repeated. */
  resync(): void {
    if (this.resyncQueued) {
      return;
    }
    this.resyncQueued = true;
    void this.schedule(
      () =>
        this.hooks.resync().finally(() => {
          this.resyncQueued = false;
        }),
      this.hooks.report,
    );
  }

  private schedule(task: Task, onFailure: (reason: unknown) => void): Promise<void> {
    const run = this.tail.then(task).then(() => undefined, onFailure);
    this.tail = run;
    return run;
  }
}
