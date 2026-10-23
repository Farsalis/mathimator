import type { WorkerEvent } from "./types";

export interface Handshake {
  previewJobId: string | null;
  previewEpoch: number;
  epoch: number;
  exportJobId: string | null;
  exportCommandSent: boolean;
  pendingCancel: boolean;
  status: string;
  exporting: boolean;
  exportLabel: string;
  exportNote: string | null;
  exportPath: string | null;
  error: string | null;
  rendering: boolean;
  buffer: WorkerEvent[];
}

export interface HandshakeStep {
  session: Handshake;
  applied: WorkerEvent[];
  cancelJobId: string | null;
}

export function createHandshake(): Handshake {
  return {
    previewJobId: null,
    previewEpoch: 0,
    epoch: 0,
    exportJobId: null,
    exportCommandSent: false,
    pendingCancel: false,
    status: "Starting",
    exporting: false,
    exportLabel: "Export",
    exportNote: null,
    exportPath: null,
    error: null,
    rendering: false,
    buffer: [],
  };
}

export function armExportCancel(session: Handshake): Handshake {
  return { ...session, pendingCancel: true };
}

export function beginExport(session: Handshake, jobId: string): HandshakeStep {
  const reserved: Handshake = {
    ...session,
    exportJobId: jobId,
    exportCommandSent: false,
    exporting: true,
    exportLabel: "Rendering export",
    status: "Rendering export",
    error: null,
    exportNote: null,
  };
  return flush(reserved, jobId);
}

export function markExportSent(session: Handshake): HandshakeStep {
  const sent: Handshake = { ...session, exportCommandSent: true };
  if (sent.pendingCancel && sent.exportJobId !== null) {
    return { session: { ...sent, pendingCancel: false }, applied: [], cancelJobId: sent.exportJobId };
  }
  return { session: sent, applied: [], cancelJobId: null };
}

export function requestCancel(session: Handshake): HandshakeStep {
  if (session.exportJobId === null) {
    return { session, applied: [], cancelJobId: null };
  }
  if (!session.exportCommandSent) {
    return { session: { ...session, pendingCancel: true }, applied: [], cancelJobId: null };
  }
  return { session, applied: [], cancelJobId: session.exportJobId };
}

export function beginPreview(session: Handshake, jobId: string, epoch: number): HandshakeStep {
  const reserved: Handshake = {
    ...session,
    previewJobId: jobId,
    previewEpoch: epoch,
    epoch,
    rendering: true,
    status: session.exportJobId !== null ? session.status : "Rendering preview",
  };
  return flush(reserved, jobId);
}

export function receive(session: Handshake, message: WorkerEvent): HandshakeStep {
  const next = applyWorkerEvent(session, message);
  if (next.buffer.length > session.buffer.length) {
    return { session: next, applied: [], cancelJobId: null };
  }
  return { session: next, applied: [message], cancelJobId: null };
}

function flush(session: Handshake, jobId: string): HandshakeStep {
  const mine = session.buffer.filter((event) => "jobId" in event && event.jobId === jobId);
  let next: Handshake = {
    ...session,
    buffer: session.buffer.filter((event) => !("jobId" in event) || event.jobId !== jobId),
  };
  const applied: WorkerEvent[] = [];
  for (const event of mine) {
    next = applyWorkerEvent(next, event);
    applied.push(event);
  }
  return { session: next, applied, cancelJobId: null };
}

function applyWorkerEvent(session: Handshake, message: WorkerEvent): Handshake {
  if (message.event === "done" || message.event === "error" || message.event === "beat_ready") {
    const id = message.jobId;
    const known = id !== null && (id === session.previewJobId || id === session.exportJobId);
    if (id !== null && !known) {
      return { ...session, buffer: [...session.buffer, message] };
    }
  }
  switch (message.event) {
    case "ready":
      if (session.previewJobId === null && session.exportJobId === null) {
        return { ...session, status: "Ready" };
      }
      return session;
    case "beat_ready":
      if (message.jobId !== session.previewJobId || session.epoch !== session.previewEpoch) {
        return session;
      }
      if (session.exportJobId !== null) {
        return session;
      }
      return { ...session, status: `Rendering preview — beat ${message.beatIndex + 1}` };
    case "done":
      if (message.jobId === session.previewJobId && session.epoch === session.previewEpoch) {
        return {
          ...session,
          previewJobId: null,
          rendering: false,
          status: session.exportJobId !== null ? session.status : "Ready",
        };
      }
      if (message.jobId === session.exportJobId) {
        return {
          ...session,
          exportJobId: null,
          exportCommandSent: false,
          pendingCancel: false,
          exporting: false,
          exportLabel: "Export",
          exportPath: message.path,
          exportNote: message.current === false ? "This file is from an older export." : null,
          status: "Ready",
        };
      }
      return session;
    case "error":
      return applyError(session, message.jobId, message.message);
    case "rejected":
      return session;
    default: {
      const unexpected: never = message;
      throw new Error(`Unhandled worker event ${JSON.stringify(unexpected)}`);
    }
  }
}

function applyError(session: Handshake, jobId: string | null, message: string): Handshake {
  if (jobId !== null && jobId !== session.previewJobId && jobId !== session.exportJobId) {
    return session;
  }
  let next = session;
  if (message === "cancelled") {
    if (!(jobId === session.previewJobId && session.epoch !== session.previewEpoch)) {
      next = { ...next, status: "Cancelled" };
    }
  } else if (jobId === null || session.epoch === session.previewEpoch || jobId === session.exportJobId) {
    next = { ...next, error: message, status: "Ready" };
  }
  if (jobId === session.previewJobId) {
    next = { ...next, previewJobId: null, rendering: false };
  }
  if (jobId === session.exportJobId) {
    next = {
      ...next,
      exportJobId: null,
      exportCommandSent: false,
      pendingCancel: false,
      exporting: false,
      exportLabel: "Export",
    };
  }
  return next;
}
