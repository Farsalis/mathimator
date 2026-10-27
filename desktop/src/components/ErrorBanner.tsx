import { useState } from "react";
import type { Failure } from "../workerQueue";

/** The worker's message when a render used a cut-off clip and removed it, so the next render redoes its beat. */
const CLIP_REMOVED = /^manim used an incomplete clip .+; it was removed, so render again$/;

interface ErrorBannerProps {
  failure: Failure;
  /** Save As is offered when the project folder is gone. */
  canSaveAs: boolean;
  onSaveAs: () => void;
  /** Render again is offered when a preview used an incomplete clip that the worker removed. */
  canRenderAgain: boolean;
  onRenderAgain: () => void;
  onDismiss: () => void;
}

/** The error banner: the message as a short summary, the worker's output on request, and Dismiss. */
export function ErrorBanner({
  failure,
  canSaveAs,
  onSaveAs,
  canRenderAgain,
  onRenderAgain,
  onDismiss,
}: ErrorBannerProps) {
  // Keyed by the failure, so a new failure starts with its details hidden.
  const [expandedFor, setExpandedFor] = useState<Failure | null>(null);
  const expanded = expandedFor === failure;

  return (
    <div className="banner" role="alert" data-testid="error-banner">
      <div className="banner-row">
        <p className="banner-message" data-testid="error">
          {failure.message}
        </p>
        {failure.code === "project_unavailable" ? (
          <button type="button" data-testid="banner-save-as" disabled={!canSaveAs} onClick={onSaveAs}>
            Save As…
          </button>
        ) : null}
        {rendersAgain(failure) ? (
          <button type="button" data-testid="banner-render-again" disabled={!canRenderAgain} onClick={onRenderAgain}>
            Render again
          </button>
        ) : null}
        {failure.details ? (
          <button
            type="button"
            data-testid="error-details-toggle"
            aria-expanded={expanded}
            onClick={() => setExpandedFor(expanded ? null : failure)}
          >
            {expanded ? "Hide details" : "Show details"}
          </button>
        ) : null}
        <button type="button" data-testid="dismiss-error" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
      {expanded ? (
        <pre className="banner-details" data-testid="error-details">
          {failure.details}
        </pre>
      ) : null}
    </div>
  );
}

/**
 * Whether a preview failed on an incomplete clip that is gone now, so a new preview can succeed. An export that failed
 * so is exported again, and a clip that could not be removed would fail the next render as well.
 */
function rendersAgain(failure: Failure): boolean {
  return failure.job === "preview" && failure.code === "render_failed" && CLIP_REMOVED.test(failure.message);
}
