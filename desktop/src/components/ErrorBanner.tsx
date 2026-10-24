import { useState } from "react";
import type { Failure } from "../workerQueue";

interface ErrorBannerProps {
  failure: Failure;
  /** Save As is offered when the project folder is gone (D57). */
  canSaveAs: boolean;
  onSaveAs: () => void;
  onDismiss: () => void;
}

/** The error banner (C9.3, D23): the message as a short summary, the worker's output on request, and Dismiss. */
export function ErrorBanner({ failure, canSaveAs, onSaveAs, onDismiss }: ErrorBannerProps) {
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
