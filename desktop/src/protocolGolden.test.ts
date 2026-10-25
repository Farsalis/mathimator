import { invoke } from "@tauri-apps/api/core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import golden from "../../tests/fixtures/protocol/replies.json";
import {
  beginExport,
  beginPreview,
  createHandshake,
  loaded,
  receive,
  requestExport,
  type Handshake,
  type HandshakeStep,
} from "./jobSession";
import { parseEditorState, parseWorkerEvent, type WorkerEvent } from "./types";
import { invokeWorker, WorkerRefusal } from "./workerQueue";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const invokeMock = vi.mocked(invoke);

/**
 * A request line of requests.json and the lines the worker wrote for it, folders written as placeholders. The worker
 * was started as the shell starts it (tests/test_shell_fixture.py), so these are the lines the shell routes (C9.4).
 */
interface Entry {
  line: { id?: string; cmd: string };
  replies: Record<string, unknown>[];
}

const entries: Entry[] = golden.requests;
/** The requests the shell only writes: every line of theirs is a `worker-event`, as is a line without an id (C9.4). */
const SENT_ONLY = new Set(["render_preview", "export", "cancel"]);
const broadcast = entries.filter((entry) => SENT_ONLY.has(entry.line.cmd) || entry.line.id === undefined);
/** The requests whose reply the shell hands to invoke: all the others but its own handshake. */
const answered = entries.filter((entry) => !broadcast.includes(entry) && entry.line.cmd !== "status");
const idle = loaded(createHandshake());

beforeEach(() => {
  invokeMock.mockReset();
});

/** The job of the `nth` request with the command `cmd`: its id and its lines. */
function jobOf(cmd: string, nth = 0): { id: string; replies: Record<string, unknown>[] } {
  const entry = entries.filter((candidate) => candidate.line.cmd === cmd)[nth];
  if (entry?.line.id === undefined) {
    throw new Error(`replies.json has no request ${nth} of ${cmd}`);
  }
  return { id: entry.line.id, replies: entry.replies };
}

/** A line as the UI reads it from the `worker-event` channel; it must be an event the UI reads. */
function eventOf(reply: Record<string, unknown>): WorkerEvent {
  const event = parseWorkerEvent(reply);
  if (event === null) {
    throw new Error(`the UI does not read ${JSON.stringify(reply)}`);
  }
  return event;
}

/** Applies each line in turn as the UI applies a `worker-event`, and returns every step. */
function drive(session: Handshake, replies: Record<string, unknown>[]): HandshakeStep[] {
  const steps: HandshakeStep[] = [];
  for (const reply of replies) {
    steps.push(receive(steps.at(-1)?.session ?? session, eventOf(reply)));
  }
  return steps;
}

describe("the worker's recorded replies (replies.json)", () => {
  it("are classified as invokeWorker classifies the reply of any command, and every state parses", async () => {
    const kinds = new Set<unknown>();
    for (const reply of answered.flatMap((entry) => entry.replies)) {
      kinds.add(reply.event);
      invokeMock.mockResolvedValueOnce(reply);
      const outcome = await invokeWorker("get_state").catch((reason: unknown) => reason);
      if (reply.event === "rejected") {
        expect(outcome).toBeInstanceOf(WorkerRefusal);
        expect(outcome).toMatchObject({ code: reply.code, message: reply.message, field: reply.field });
      } else {
        expect(outcome).toBe(reply);
      }
      if (reply.event === "state") {
        expect(parseEditorState(reply).projectPath).toMatch(/^\{(project|other)\}$/);
      }
    }
    expect([...kinds].sort()).toEqual(["code", "rejected", "state"]);
  });

  it("parses every broadcast line as the event it is", () => {
    for (const reply of broadcast.flatMap((entry) => entry.replies)) {
      expect(eventOf(reply)).toMatchObject({ event: reply.event, jobId: reply.jobId });
    }
  });

  it("ignores a reply that arrives after its request timed out, or reads it as a refusal of no current job", () => {
    const busy = beginPreview(idle, jobOf("render_preview").id);
    for (const reply of entries.filter((entry) => !broadcast.includes(entry)).flatMap((entry) => entry.replies)) {
      const event = parseWorkerEvent(reply);
      if (event !== null) {
        expect(event.event).toBe("rejected");
        expect(receive(busy, event).session).toBe(busy);
      }
    }
  });

  it("drives the preview to Ready, patching each beat into the index", () => {
    const preview = jobOf("render_preview");
    const steps = drive(beginPreview(idle, preview.id), preview.replies);
    expect(steps.map((step) => step.session.status)).toEqual([
      "Rendering preview",
      "Rendering preview — beat 1",
      "Rendering preview — beat 2",
      "Ready",
    ]);
    const beats = steps.flatMap((step) => (step.beat === null ? [] : [step.beat]));
    expect(beats.map((beat) => beat.beatIndex)).toEqual([0, 1]);
    for (const beat of beats) {
      expect(beat.path.startsWith("{project}")).toBe(true);
    }
    expect(steps.at(-1)).toMatchObject({ session: { previewJobId: null }, failure: null, next: "refresh" });
  });

  it("drives an export to Ready with the file it published, then a cached export without a start", () => {
    const exported = jobOf("export", 0);
    const steps = drive(beginExport(requestExport(idle), exported.id), exported.replies);
    expect(steps.map((step) => step.session.status)).toEqual(["Rendering export", "Ready"]);
    const done = steps.at(-1)?.session;
    expect(done).toMatchObject({ exporting: false, exportJobId: null, note: null });
    expect(done?.exportPath?.startsWith("{project}")).toBe(true);

    const cached = jobOf("export", 1);
    const again = drive(beginExport(requestExport(done ?? idle), cached.id), cached.replies);
    expect(again.map((step) => step.session.status)).toEqual(["Ready"]);
    expect(again.at(-1)?.session).toMatchObject({ exporting: false, exportPath: done?.exportPath, note: null });
  });

  it("leaves the job handshake alone for a refused cancel and an error without an id", () => {
    const busy = beginExport(requestExport(idle), jobOf("export").id);
    const lines = broadcast
      .filter((entry) => !["render_preview", "export"].includes(entry.line.cmd))
      .flatMap((entry) => entry.replies);
    expect(lines.map((line) => line.event)).toEqual(["rejected", "error"]);
    for (const step of drive(busy, lines)) {
      expect(step).toEqual({ session: busy, failure: null, beat: null, next: null });
    }
  });
});
