import { describe, expect, it } from "vitest";
import fixture from "../../tests/fixtures/protocol/commands.json";
import { SHELL_COMMANDS, WORKER_COMMANDS } from "./commands";

/** The shared command list (C9.1) without the debug-only commands, which the UI never sends. */
const sent = fixture.tauriCommands.filter((command) => command.debugOnly !== true);

/** The argument names of the listed commands that reach the worker, or of those that do not. */
function argsByName(toWorker: boolean): Record<string, string[]> {
  return Object.fromEntries(
    sent.filter((command) => (command.worker !== null) === toWorker).map((command) => [command.name, command.args]),
  );
}

describe("command names (C9.1)", () => {
  it("sends the worker-bound commands of commands.json through invokeWorker, with its argument names", () => {
    expect(WORKER_COMMANDS).toEqual(argsByName(true));
  });

  it("invokes the shell's own commands of commands.json with its argument names", () => {
    expect(SHELL_COMMANDS).toEqual(argsByName(false));
  });

  it("has no command that reaches the worker requests the shell sends itself: the handshake and the TeX state", () => {
    const reached = new Set(fixture.tauriCommands.map((command) => command.worker));
    expect(Object.keys(fixture.workerRequests).filter((name) => !reached.has(name))).toEqual([
      "status",
      "set_tex_error",
    ]);
  });
});
