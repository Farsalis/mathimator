import { invoke } from "@tauri-apps/api/core";
import type { ArgsOf, WorkerCommand } from "./commands";
import { isRecord } from "./parse";
import { parseWorkerEvent } from "./types";

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

/** A failure as the error banner shows it (C9.3, D23). `code` is null for a failure that has none. */
export interface Failure {
  code: string | null;
  message: string;
  details?: string;
  /** The preview or export whose terminal line this is (C9.3). A failed task has none. */
  job?: "preview" | "export";
}

export function failureOf(reason: unknown): Failure {
  if (reason instanceof WorkerRefusal) {
    return { code: reason.code, message: reason.message };
  }
  if (reason instanceof WorkerFailure) {
    return { code: reason.code, message: reason.message, details: reason.details };
  }
  return { code: null, message: errorMessage(reason) };
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
export async function invokeWorker<C extends WorkerCommand>(command: C, ...args: ArgsOf<C>): Promise<unknown> {
  const reply = await invoke<unknown>(command, ...args).catch((reason: unknown) => {
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

export type Task = () => Promise<unknown>;

export interface QueueHooks {
  /** Shows why a task failed. */
  report: (reason: unknown) => void;
  /** Reloads the worker's state with `get_state` and applies it. */
  resync: () => Promise<void>;
  /** A task was refused with `project_changed` and the queue is paused (C9.3). Overwrite runs `retry` again. */
  changed: (refusal: WorkerRefusal, retry: Task) => void;
}

interface Entry {
  task: Task;
  onFailure: (reason: unknown) => void;
  settle: () => void;
}

/**
 * The one ordered queue of worker tasks (D20, C9.2). Tasks run one at a time in call order, so at most one worker
 * request is outstanding and each task applies its results before the next one starts. A failed task is reported
 * and never blocks later ones. A timeout also queues the `get_state` resync, at most one queued at a time (F164).
 * The one exception is a `project_changed` refusal: it pauses the queue until the user answers the prompt (C9.3).
 */
export class WorkerQueue {
  private readonly waiting: Entry[] = [];
  private running = false;
  private paused = false;
  private resyncQueued = false;

  constructor(private readonly hooks: QueueHooks) {}

  /**
   * Queues `task`. If it is refused with `project_changed`, `retry` is what Overwrite runs again: the task itself,
   * unless it must start differently the second time. The returned promise settles once the task has finished, and
   * never rejects.
   */
  enqueue(task: Task, retry: Task = task): Promise<void> {
    return new Promise((resolve) => {
      this.waiting.push({ task, onFailure: (reason) => this.failed(reason, retry), settle: () => resolve() });
      this.next();
    });
  }

  /** Starts no new task until `resume`. The task in flight finishes (C9.3). */
  pause(): void {
    this.paused = true;
  }

  /** Starts tasks again, running `first` in order ahead of every task that waited. */
  resume(first: readonly Task[] = []): void {
    const entries = first.map((task) => ({
      task,
      onFailure: (reason: unknown) => this.failed(reason, task),
      settle: () => undefined,
    }));
    this.waiting.unshift(...entries);
    this.paused = false;
    this.next();
  }

  /**
   * Queues the resync unless one is already queued. A request while it runs queues another, because that run may
   * fail with the exit that prompted the request (C8.6). A resync that fails is reported, not repeated.
   */
  resync(): void {
    if (this.resyncQueued) {
      return;
    }
    this.resyncQueued = true;
    const task = (): Promise<void> => {
      this.resyncQueued = false;
      return this.hooks.resync();
    };
    this.waiting.push({ task, onFailure: this.hooks.report, settle: () => undefined });
    this.next();
  }

  private failed(reason: unknown, retry: Task): void {
    if (reason instanceof WorkerRefusal && reason.code === "project_changed") {
      this.pause();
      this.hooks.changed(reason, retry);
      return;
    }
    this.hooks.report(reason);
    if (reason instanceof WorkerFailure && reason.code === "timeout") {
      this.resync();
    }
  }

  /** Starts the next waiting task, unless one is running or the queue is paused. */
  private next(): void {
    if (this.running || this.paused) {
      return;
    }
    const entry = this.waiting.shift();
    if (!entry) {
      return;
    }
    this.running = true;
    void Promise.resolve()
      .then(entry.task)
      .then(() => undefined, entry.onFailure)
      .finally(() => {
        this.running = false;
        entry.settle();
        this.next();
      });
  }
}
