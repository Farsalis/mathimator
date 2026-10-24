import { invoke } from "@tauri-apps/api/core";

/** The names of each command's arguments, as the UI passes them. */
type CommandTable = Readonly<Record<string, readonly string[]>>;

/**
 * The worker-bound shell commands (C9.1). Each one goes through `invokeWorker` inside a queued task (C9.2). This table
 * and SHELL_COMMANDS are the UI's half of tests/fixtures/protocol/commands.json, which commands.test.ts checks.
 */
export const WORKER_COMMANDS = {
  get_state: [],
  edit_document: ["edit"],
  undo_document: [],
  redo_document: [],
  clear_cache: [],
  reload_project: [],
  overwrite_project: [],
  new_project: [],
  open_sample: [],
  open_project_dialog: [],
  save_project_as_dialog: [],
  export_code_dialog: [],
  render_preview: ["jobId"],
  export_movie: ["jobId", "force"],
  cancel_render: ["jobId"],
} as const satisfies CommandTable;

/** The shell commands that the shell answers itself (C9.1). */
export const SHELL_COMMANDS = {
  app_status: [],
  restart_worker: [],
  dismiss_notice: ["id"],
  reserve_job_id: ["prefix"],
  compile_equation_preview: ["latex", "slot", "generation"],
  cancel_equation_preview: ["slot", "generation"],
  note_shell_ready: [],
} as const satisfies CommandTable;

export type WorkerCommand = keyof typeof WORKER_COMMANDS;
type ShellCommand = keyof typeof SHELL_COMMANDS;

type Commands = typeof WORKER_COMMANDS & typeof SHELL_COMMANDS;

/** The arguments of command `C`: none, or one object with exactly the names its table lists. */
export type ArgsOf<C extends keyof Commands> = Commands[C] extends readonly []
  ? []
  : [args: Record<Commands[C][number], unknown>];

/** Invokes a command that the shell answers itself. */
export function invokeShell<C extends ShellCommand>(command: C, ...args: ArgsOf<C>): Promise<unknown> {
  return invoke(command, ...args);
}
