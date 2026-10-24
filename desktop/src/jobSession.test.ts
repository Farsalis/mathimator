import { describe, expect, it } from "vitest";
import {
  armExportCancel,
  beginExport,
  beginPreview,
  createHandshake,
  markExportSent,
  receive,
  requestCancel,
} from "./jobSession";
import { patchBeat, playbackAt, shouldSeek } from "./playback";
import type { EditorState, PreviewIndex } from "./types";

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

describe("export handshake", () => {
  it("applies a done event that arrived before the export id was stored", () => {
    const early = receive(createHandshake(), {
      event: "done",
      jobId: "export-1",
      path: "C:/exports/scene.mp4",
      current: true,
    });
    expect(early.applied).toEqual([]);
    expect(early.session.status).toBe("Starting");
    const begun = beginExport(early.session, "export-1");
    expect(begun.applied.map((event) => event.event)).toEqual(["done"]);
    expect(begun.session.exporting).toBe(false);
    expect(begun.session.status).toBe("Ready");
    expect(begun.session.exportPath).toBe("C:/exports/scene.mp4");
    expect(begun.session.exportNote).toBeNull();
  });

  it("remembers cancel that happens before the export command is sent", () => {
    const armed = armExportCancel(createHandshake());
    const begun = beginExport(armed, "export-2");
    const waiting = requestCancel(begun.session);
    expect(waiting.cancelJobId).toBeNull();
    expect(waiting.session.pendingCancel).toBe(true);
    const sent = markExportSent(waiting.session);
    expect(sent.cancelJobId).toBe("export-2");
    expect(sent.session.pendingCancel).toBe(false);
  });

  it("labels a finished file that is not the current export", () => {
    const begun = beginExport(createHandshake(), "export-3");
    const sent = markExportSent(begun.session);
    const done = receive(sent.session, {
      event: "done",
      jobId: "export-3",
      path: "C:/exports/old.mp4",
      current: false,
    });
    expect(done.session.exportNote).toBe("This file is from an older export.");
    expect(done.session.status).toBe("Ready");
    expect(done.session.exporting).toBe(false);
  });

  it("does not replace Rendering export when a preview is reserved", () => {
    const begun = beginExport(createHandshake(), "export-4");
    const preview = beginPreview(begun.session, "preview-4", 1);
    expect(preview.session.status).toBe("Rendering export");
    expect(preview.session.exportJobId).toBe("export-4");
    expect(preview.session.previewJobId).toBe("preview-4");
  });

  it("keeps Rendering export when a preview fails during the export", () => {
    const begun = beginExport(createHandshake(), "export-5");
    const preview = beginPreview(begun.session, "preview-5", 1);
    const failed = receive(preview.session, {
      event: "error",
      jobId: "preview-5",
      code: "worker_exited",
      message: "The worker stopped (exit code 1).",
    });
    expect(failed.session.status).toBe("Rendering export");
    expect(failed.session.error).toBe("The worker stopped (exit code 1).");
    expect(failed.session.previewJobId).toBeNull();
    expect(failed.session.rendering).toBe(false);
    expect(failed.session.exporting).toBe(true);
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
