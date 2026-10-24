import { describe, expect, it } from "vitest";
import {
  abandonPreview,
  abortExport,
  beginExport,
  beginPreview,
  createHandshake,
  loaded,
  markStale,
  openReset,
  receive,
  requestCancel,
  requestExport,
  withNote,
  type Handshake,
  type HandshakeStep,
} from "./jobSession";
import { patchBeat, playbackAt, shouldSeek } from "./playback";
import type { EditorState, PreviewIndex, WorkerEvent } from "./types";

const index: PreviewIndex = {
  output: "C:/old/preview.mp4",
  beats: [
    {
      id: "beat_1",
      index: 0,
      derivedStart: 0,
      duration: 2,
      partial: "C:/old/partial-0.mp4",
      lastGood: null,
      fresh: false,
    },
    {
      id: "beat_2",
      index: 1,
      derivedStart: 2,
      duration: 2,
      partial: "C:/old/partial-1.mp4",
      lastGood: null,
      fresh: true,
    },
  ],
};

function state(): EditorState {
  return {
    event: "state",
    projectPath: "C:/project",
    document: {
      name: "Sine",
      scene: { kind: "scene", camera: null, objects: [], beats: [], templateInstances: [] },
      exportProfile: { format: "mp4", width: 1920, height: 1080, fps: 30, transparent: false },
    },
    derived: { starts: [], total: 0, dirty: [], canUndo: false, canRedo: false },
    index,
    source: null,
  };
}

const idle = loaded(createHandshake());

/** An export clicked and recorded as `jobId`, the way the Export task records it. */
function exporting(session: Handshake, jobId: string): Handshake {
  return beginExport(requestExport(session), jobId);
}

/** Applies `events` in order and returns the last step. */
function receiveAll(session: Handshake, ...events: WorkerEvent[]): HandshakeStep {
  let step: HandshakeStep = { session, failure: null, beat: null, next: null };
  for (const event of events) {
    step = receive(step.session, event);
  }
  return step;
}

function cancelled(jobId: string): WorkerEvent {
  return { event: "error", jobId, code: "cancelled", message: "cancelled" };
}

describe("preview handshake", () => {
  it("shows the current preview's beats and patches them into the index", () => {
    const begun = beginPreview(idle, "preview-1");
    expect(begun.status).toBe("Rendering preview");
    const started = receive(begun, { event: "started", jobId: "preview-1" });
    expect(started.session.status).toBe("Rendering preview");
    const beat = receive(started.session, {
      event: "beat_ready",
      jobId: "preview-1",
      beatIndex: 2,
      path: "C:/p/2.mp4",
    });
    expect(beat.session.status).toBe("Rendering preview — beat 3");
    expect(beat.beat).toEqual({ beatIndex: 2, path: "C:/p/2.mp4" });
  });

  it("ignores a beat_ready of a preview overtaken by an applied undo, but still ends it (F135)", () => {
    const stale = markStale(beginPreview(idle, "preview-2"));
    const beat = receive(stale, { event: "beat_ready", jobId: "preview-2", beatIndex: 0, path: "C:/p/0.mp4" });
    expect(beat.beat).toBeNull();
    expect(beat.session).toBe(stale);
    const done = receive(stale, { event: "done", jobId: "preview-2", path: "C:/p/preview.mp4" });
    expect(done.session.previewJobId).toBeNull();
    expect(done.session.status).toBe("Ready");
    expect(done.next).toBe("refresh");
  });

  it("starts a newly recorded preview fresh", () => {
    const next = beginPreview(markStale(beginPreview(idle, "preview-3")), "preview-4");
    const beat = receive(next, { event: "beat_ready", jobId: "preview-4", beatIndex: 0, path: "C:/p/0.mp4" });
    expect(beat.beat).toEqual({ beatIndex: 0, path: "C:/p/0.mp4" });
  });

  it("drops events for an id it does not wait for, errors without an id and the shell's own lines (F048)", () => {
    const begun = beginPreview(idle, "preview-6");
    for (const event of [
      cancelled("preview-5"),
      { event: "done", jobId: "export-9", path: "C:/p/old.mp4", current: true },
      { event: "beat_ready", jobId: "preview-5", beatIndex: 0, path: "C:/p/0.mp4" },
      { event: "error", jobId: null, code: "bad_request", message: "missing id" },
      { event: "notice", id: 1, message: "Could not reopen C:/old: gone. A new project was started." },
      { event: "tex_status", state: "failed", message: "Formulas and axis labels cannot render on this PC." },
    ] satisfies WorkerEvent[]) {
      expect(receive(begun, event)).toEqual({ session: begun, failure: null, beat: null, next: null });
    }
  });

  it("ends a preview that the worker cancelled, and never says Cancelled (F041)", () => {
    const step = receive(beginPreview(idle, "preview-7"), cancelled("preview-7"));
    expect(step.session.previewJobId).toBeNull();
    expect(step.session.status).toBe("Ready");
    expect(step.failure).toBeNull();
  });

  it("decides on the code, not the message (F090)", () => {
    const step = receive(beginPreview(idle, "preview-8"), {
      event: "error",
      jobId: "preview-8",
      code: "render_failed",
      message: "cancelled",
      details: "tail",
    });
    expect(step.session.status).toBe("Ready");
    expect(step.failure).toEqual({ job: "preview", code: "render_failed", message: "cancelled", details: "tail" });
  });

  it("ends a preview on a terminal refusal", () => {
    const step = receive(beginPreview(idle, "preview-9"), {
      event: "rejected",
      jobId: "preview-9",
      code: "tex_unavailable",
      message: "The bundled TeX is missing.",
    });
    expect(step.session.previewJobId).toBeNull();
    expect(step.session.status).toBe("Ready");
    expect(step.failure).toEqual({ job: "preview", code: "tex_unavailable", message: "The bundled TeX is missing." });
  });

  it("carries an error in its own step only (F109)", () => {
    const failed = receive(beginPreview(idle, "preview-10"), {
      event: "error",
      jobId: "preview-10",
      code: "render_failed",
      message: "LaTeX failed.",
    });
    expect(failed.failure?.message).toBe("LaTeX failed.");
    const next = beginPreview(failed.session, "preview-11");
    const beat = receive(next, { event: "beat_ready", jobId: "preview-11", beatIndex: 0, path: "C:/p/0.mp4" });
    const done = receive(beat.session, { event: "done", jobId: "preview-11", path: "C:/p/preview.mp4" });
    expect([beat.failure, done.failure]).toEqual([null, null]);
  });

  it("waits for the previous render, then renders the preview", () => {
    const begun = beginPreview(idle, "preview-12");
    const queued = receive(begun, {
      event: "queued",
      jobId: "preview-12",
      code: "stopping_previous",
      message: "Waiting for the previous render to stop.",
    });
    expect(queued.session.status).toBe("Waiting for the previous render to stop");
    expect(receive(queued.session, { event: "started", jobId: "preview-12" }).session.status).toBe("Rendering preview");
  });

  it("ends a preview whose send failed and leaves a newer one alone (F113)", () => {
    const abandoned = abandonPreview(beginPreview(idle, "preview-13"), "preview-13");
    expect(abandoned.previewJobId).toBeNull();
    expect(abandoned.status).toBe("Ready");
    const newer = beginPreview(abandoned, "preview-14");
    expect(abandonPreview(newer, "preview-13")).toBe(newer);
  });
});

describe("export handshake", () => {
  it("shows Rendering export from the click, before an id is recorded", () => {
    const clicked = requestExport(withNote(idle, "This file is from an older export."));
    expect(clicked.status).toBe("Rendering export");
    expect(clicked.exporting).toBe(true);
    expect(clicked.exportJobId).toBeNull();
    expect(clicked.note).toBeNull();
  });

  it("keeps Rendering export when the export pre-empts the preview (F041)", () => {
    const begun = exporting(beginPreview(idle, "preview-1"), "export-1");
    const step = receive(begun, cancelled("preview-1"));
    expect(step.session.status).toBe("Rendering export");
    expect(step.session.exporting).toBe(true);
    expect(step.session.previewJobId).toBeNull();
    expect(step.failure).toBeNull();
  });

  it("says Cancelled only after Cancel export", () => {
    const begun = exporting(idle, "export-2");
    const cancel = receive(requestCancel(begun), cancelled("export-2"));
    expect(cancel.session.status).toBe("Cancelled");
    expect(cancel.session.exporting).toBe(false);
    expect(cancel.session.cancelRequested).toBe(false);
    expect(receive(begun, cancelled("export-2")).session.status).toBe("Ready");
  });

  it("ends the export on a project_changed refusal and asks for the prompt", () => {
    const step = receive(exporting(idle, "export-3"), {
      event: "rejected",
      jobId: "export-3",
      code: "project_changed",
      message: "project.json was changed outside Mathimator.",
    });
    expect(step.session.exporting).toBe(false);
    expect(step.session.exportJobId).toBeNull();
    expect(step.session.status).toBe("Ready");
    expect(step.failure).toMatchObject({ job: "export", code: "project_changed" });
  });

  it("waits for the previous render, then renders the export", () => {
    const queued = receive(exporting(idle, "export-4"), {
      event: "queued",
      jobId: "export-4",
      code: "stopping_previous",
      message: "Waiting for the previous render to stop.",
    });
    expect(queued.session.status).toBe("Waiting for the previous render to stop");
    expect(receive(queued.session, { event: "started", jobId: "export-4" }).session.status).toBe("Rendering export");
  });

  it("never patches the preview index from an export's beat_ready (F032, F033)", () => {
    const begun = exporting(beginPreview(idle, "preview-5"), "export-5");
    const step = receive(begun, { event: "beat_ready", jobId: "export-5", beatIndex: 0, path: "C:/e/0.mp4" });
    expect(step.beat).toBeNull();
    expect(step.session).toBe(begun);
  });

  it("labels a finished file that is not the current export", () => {
    const old = receive(exporting(idle, "export-6"), {
      event: "done",
      jobId: "export-6",
      path: "C:/exports/old.mp4",
      current: false,
    });
    expect(old.session.note).toBe("This file is from an older export.");
    expect(old.session.exportPath).toBe("C:/exports/old.mp4");
    expect(old.session.status).toBe("Ready");
    expect(old.session.exporting).toBe(false);
    for (const current of [true, undefined]) {
      const done = receive(exporting(old.session, "export-7"), {
        event: "done",
        jobId: "export-7",
        path: "C:/exports/new.mp4",
        current,
      });
      expect(done.session.note).toBeNull();
    }
  });

  it("does not replace Rendering export when a preview is recorded", () => {
    const preview = beginPreview(exporting(idle, "export-8"), "preview-8");
    expect(preview.status).toBe("Rendering export");
    expect(preview.exportJobId).toBe("export-8");
    expect(preview.previewJobId).toBe("preview-8");
  });

  it("keeps Rendering export when a preview fails during the export", () => {
    const preview = beginPreview(exporting(idle, "export-9"), "preview-9");
    const failed = receive(preview, {
      event: "rejected",
      jobId: "preview-9",
      code: "read_failed",
      message: "Could not read project.json: access denied.",
    });
    expect(failed.session.status).toBe("Rendering export");
    expect(failed.failure?.code).toBe("read_failed");
    expect(failed.session.previewJobId).toBeNull();
    expect(failed.session.exporting).toBe(true);
  });

  it("stops the Export task with Cancelled when Cancel came before the id was recorded", () => {
    const begun = beginExport(requestCancel(requestExport(idle)), "export-10");
    expect(begun.cancelRequested).toBe(true);
    const stopped = abortExport(begun, "Cancelled");
    expect(stopped.status).toBe("Cancelled");
    expect(stopped.exporting).toBe(false);
    expect(stopped.exportJobId).toBeNull();
  });

  it("does not carry a Cancel into the next export after a refused profile (F047)", () => {
    const refused = abortExport(requestCancel(exporting(idle, "export-11")), "Ready");
    expect(refused.status).toBe("Ready");
    expect(refused.cancelRequested).toBe(false);
    const next = exporting(refused, "export-12");
    expect(next.cancelRequested).toBe(false);
    expect(receive(next, cancelled("export-12")).session.status).toBe("Ready");
  });
});

describe("automatic preview after an export (C7.5)", () => {
  it("follows an export that started while a preview was recorded", () => {
    const begun = exporting(beginPreview(idle, "preview-1"), "export-1");
    const done = receiveAll(begun, cancelled("preview-1"), {
      event: "done",
      jobId: "export-1",
      path: "C:/e/1.mp4",
      current: true,
    });
    expect(done.next).toBe("preview");
    expect(done.session.preempted).toBe(false);
  });

  it("follows an export during which the preview was cancelled", () => {
    const held = beginPreview(exporting(idle, "export-2"), "preview-2");
    const failed = receiveAll(held, cancelled("preview-2"), {
      event: "error",
      jobId: "export-2",
      code: "render_failed",
      message: "manim exited with status 1: ValueError: bad",
    });
    expect(failed.next).toBe("preview");
  });

  it("starts none when a preview is current or nothing was pre-empted", () => {
    const begun = exporting(beginPreview(idle, "preview-3"), "export-3");
    const newer = beginPreview(receive(begun, cancelled("preview-3")).session, "preview-4");
    const done: WorkerEvent = { event: "done", jobId: "export-3", path: "C:/e/3.mp4", current: true };
    expect(receive(newer, done).next).toBeNull();
    expect(receive(exporting(idle, "export-3"), done).next).toBeNull();
  });
});

describe("worker exit and the Open reset", () => {
  it("ends every job, never says Cancelled and asks for one resync (C8.6)", () => {
    const busy = requestCancel(exporting(beginPreview(idle, "preview-1"), "export-1"));
    const exited = receive(busy, { event: "worker_exited", message: "The worker stopped (exit code 1).", exitCode: 1 });
    expect(exited.session).toMatchObject({
      previewJobId: null,
      exportJobId: null,
      exporting: false,
      cancelRequested: false,
      preempted: false,
      status: "Ready",
    });
    expect(exited.next).toBe("resync");
    expect(receive(exited.session, cancelled("export-1")).session.status).toBe("Ready");
  });

  it("clears the export result, marks the preview stale and forgets a pre-empted preview", () => {
    const shown = { ...beginPreview(idle, "preview-2"), exportPath: "C:/e/2.mp4", note: "Code exported to C:/s.py" };
    const reset = openReset(exporting(shown, "export-2"));
    expect(reset).toMatchObject({ exportPath: null, note: null, previewStale: true, preempted: false });
  });
});

describe("playback", () => {
  it("does not play the previous movie once a beat is dirty", () => {
    const fresh = {
      ...index,
      beats: index.beats.map((beat) => ({ ...beat, fresh: true })),
    };
    const choice = playbackAt(fresh, 0.2, (path) => path !== fresh.output);
    expect(choice.mode).toBe("partial");
    expect(choice.path).toBe("C:/old/partial-0.mp4");
  });

  it("holds the last beat past the end and shows nothing without beats", () => {
    expect(playbackAt(index, 9).beatIndex).toBe(1);
    expect(playbackAt({ output: null, beats: [] }, 1).mode).toBe("empty");
  });

  it("uses a blank canvas when the partial file is missing", () => {
    const choice = playbackAt(index, 2.2, () => false);
    expect(choice.mode).toBe("blank");
    expect(choice.path).toBeNull();
    expect(choice.badge).toBe(true);
  });

  it("clears the stale movie path when a partial arrives", () => {
    const next = patchBeat(state(), 0, "C:/new/partial-0.mp4");
    expect(next?.index?.output).toBeNull();
    expect(next?.index?.beats[0]?.fresh).toBe(true);
    expect(next?.index?.beats[0]?.partial).toBe("C:/new/partial-0.mp4");
  });

  it("seeks for a scrub smaller than a quarter second", () => {
    expect(shouldSeek(1, 1.05)).toBe(true);
    expect(shouldSeek(1, 1.0001)).toBe(false);
  });
});
