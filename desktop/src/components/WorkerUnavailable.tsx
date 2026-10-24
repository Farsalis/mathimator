import { useState } from "react";

interface WorkerUnavailableProps {
  reason: string;
  /** Restarts the worker. The view stays until the worker is ready. */
  onRetry: () => Promise<void>;
}

/** Shown while the worker cannot run: why, and Retry (D31, C9.5). */
export function WorkerUnavailable({ reason, onRetry }: WorkerUnavailableProps) {
  const [retrying, setRetrying] = useState(false);

  function retry(): void {
    setRetrying(true);
    void onRetry().finally(() => setRetrying(false));
  }

  return (
    <section className="unavailable" role="alert" aria-label="Worker unavailable" data-testid="worker-unavailable">
      {reason ? (
        <p className="unavailable-reason" data-testid="unavailable-reason">
          {reason}
        </p>
      ) : null}
      <button type="button" data-testid="retry-worker" disabled={retrying} onClick={retry}>
        Retry
      </button>
    </section>
  );
}
