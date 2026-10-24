import { invoke } from "@tauri-apps/api/core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ParseError } from "./parse";
import { errorMessage, invokeWorker, WorkerFailure, WorkerQueue, WorkerRefusal } from "./workerQueue";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const invokeMock = vi.mocked(invoke);

const STATE = { event: "state", jobId: "rpc-1" };
const TIMEOUT = { code: "timeout", message: "No reply within 30 s." };
const TIMEOUT_TEXT = "The worker did not answer in time.";

beforeEach(() => {
  invokeMock.mockReset();
});

function deferred(): { promise: Promise<unknown>; resolve: (value: unknown) => void } {
  let resolve: (value: unknown) => void = () => undefined;
  const promise = new Promise<unknown>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/** Lets every pending promise callback run. */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function callsOf(command: string): number {
  return invokeMock.mock.calls.filter(([name]) => name === command).length;
}

function idle(): Promise<void> {
  return Promise.resolve();
}

describe("invokeWorker", () => {
  it("returns a state reply", async () => {
    invokeMock.mockResolvedValueOnce(STATE);
    await expect(invokeWorker("get_state")).resolves.toEqual(STATE);
    expect(invokeMock).toHaveBeenCalledWith("get_state", undefined);
  });

  it("throws WorkerRefusal with the code, message and field of a rejected reply", async () => {
    invokeMock.mockResolvedValueOnce({
      event: "rejected",
      jobId: "rpc-2",
      code: "unsupported_expression",
      message: "This expression is not supported.",
      field: "expression",
    });
    const call = invokeWorker("edit_document", {
      edit: { op: "set_expression", objectId: "plot_1", expression: "x^" },
    });
    await expect(call).rejects.toBeInstanceOf(WorkerRefusal);
    await expect(call).rejects.toMatchObject({
      code: "unsupported_expression",
      message: "This expression is not supported.",
      field: "expression",
    });
  });

  it("throws WorkerFailure for an error reply", async () => {
    invokeMock.mockResolvedValueOnce({
      event: "error",
      jobId: "rpc-3",
      code: "internal",
      message: "Internal error: KeyError: 'x'. Details are in worker.log.",
      details: "Traceback",
    });
    const call = invokeWorker("clear_cache");
    await expect(call).rejects.toBeInstanceOf(WorkerFailure);
    await expect(call).rejects.toMatchObject({ code: "internal", details: "Traceback" });
  });

  it("throws WorkerFailure for a shell error object", async () => {
    invokeMock.mockRejectedValueOnce({ code: "worker_exited", message: "The worker stopped (exit code 1)." });
    const call = invokeWorker("undo_document");
    await expect(call).rejects.toBeInstanceOf(WorkerFailure);
    await expect(call).rejects.toMatchObject({ code: "worker_exited", message: "The worker stopped (exit code 1)." });
  });

  it("words a shell timeout for the user", async () => {
    invokeMock.mockRejectedValueOnce(TIMEOUT);
    await expect(invokeWorker("get_state")).rejects.toMatchObject({ code: "timeout", message: TIMEOUT_TEXT });
  });

  it("wraps a rejection that is not a shell error", async () => {
    invokeMock.mockRejectedValueOnce("command view_code not found");
    await expect(invokeWorker("view_code")).rejects.toMatchObject({
      code: "invoke_failed",
      message: "command view_code not found",
    });
  });

  it("returns null for a send-only command and a cancelled dialog", async () => {
    invokeMock.mockResolvedValueOnce(null).mockResolvedValueOnce({ cancelled: true });
    await expect(invokeWorker("render_preview", { jobId: "preview-1" })).resolves.toBeNull();
    await expect(invokeWorker("open_project_dialog")).resolves.toBeNull();
  });

  it("names the missing field of a malformed refusal", async () => {
    invokeMock.mockResolvedValueOnce({ event: "rejected", jobId: "rpc-4", message: "Nothing to undo." });
    const call = invokeWorker("undo_document");
    await expect(call).rejects.toBeInstanceOf(ParseError);
    await expect(call).rejects.toMatchObject({ field: "rejected.code" });
  });
});

describe("errorMessage", () => {
  it("reads errors, shell error objects and plain values", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
    expect(errorMessage({ code: "io", message: "disk full" })).toBe("disk full");
    expect(errorMessage("plain")).toBe("plain");
  });
});

describe("WorkerQueue", () => {
  it("applies two edits in call order and starts the second invoke after the first settles", async () => {
    const first = deferred();
    const second = deferred();
    invokeMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const queue = new WorkerQueue({ report: vi.fn(), resync: idle });
    const applied: string[] = [];
    const edit = (name: string): Promise<void> =>
      queue.enqueue(async () => {
        await invokeWorker("edit_document", { edit: { op: "set_project_name", name } });
        applied.push(name);
      });
    const both = Promise.all([edit("first"), edit("second")]);

    await flush();
    second.resolve(STATE);
    await flush();
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(applied).toEqual([]);

    first.resolve(STATE);
    await both;
    expect(invokeMock).toHaveBeenCalledTimes(2);
    expect(applied).toEqual(["first", "second"]);
  });

  it("reports a failed task and still runs the next one", async () => {
    invokeMock
      .mockResolvedValueOnce({
        event: "rejected",
        jobId: "rpc-1",
        code: "nothing_to_undo",
        message: "Nothing to undo.",
      })
      .mockResolvedValueOnce(STATE);
    const report = vi.fn();
    const queue = new WorkerQueue({ report, resync: idle });
    const ran: string[] = [];

    await Promise.all([
      queue.enqueue(async () => {
        await invokeWorker("undo_document");
        ran.push("undo");
      }),
      queue.enqueue(async () => {
        await invokeWorker("get_state");
        ran.push("get_state");
      }),
    ]);

    expect(ran).toEqual(["get_state"]);
    expect(report).toHaveBeenCalledOnce();
    expect(report.mock.calls[0]?.[0]).toBeInstanceOf(WorkerRefusal);
  });

  it("queues exactly one get_state resync for two timeouts in a row", async () => {
    invokeMock.mockRejectedValueOnce(TIMEOUT).mockRejectedValueOnce(TIMEOUT).mockResolvedValue(STATE);
    const report = vi.fn();
    const queue = new WorkerQueue({
      report,
      resync: async () => {
        await invokeWorker("get_state");
      },
    });

    void queue.enqueue(() => invokeWorker("edit_document", { edit: { op: "set_project_name", name: "A" } }));
    await queue.enqueue(() => invokeWorker("undo_document"));
    await queue.enqueue(idle);

    expect(callsOf("get_state")).toBe(1);
    expect(report.mock.calls.map(([reason]) => errorMessage(reason))).toEqual([TIMEOUT_TEXT, TIMEOUT_TEXT]);
  });

  it("does not repeat a resync that times out itself", async () => {
    invokeMock.mockRejectedValue(TIMEOUT);
    const report = vi.fn();
    const queue = new WorkerQueue({
      report,
      resync: async () => {
        await invokeWorker("get_state");
      },
    });

    await queue.enqueue(() => invokeWorker("redo_document"));
    await queue.enqueue(idle);
    await queue.enqueue(idle);

    expect(callsOf("get_state")).toBe(1);
    expect(report).toHaveBeenCalledTimes(2);
  });
});
