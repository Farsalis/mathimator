import { useEffect, useEffectEvent } from "react";

interface ProjectChangedPromptProps {
  message: string;
  onReload: () => void;
  onOverwrite: () => void;
  onCancel: () => void;
}

/**
 * The project_changed prompt: project.json changed outside Mathimator, and nothing is written until the user chooses.
 * The app behind it is inert, and Escape is Cancel.
 */
export function ProjectChangedPrompt({ message, onReload, onOverwrite, onCancel }: ProjectChangedPromptProps) {
  const onKeyDown = useEffectEvent((event: KeyboardEvent) => {
    if (event.key === "Escape") {
      onCancel();
    }
  });

  useEffect(() => {
    const listener = (event: KeyboardEvent): void => onKeyDown(event);
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);

  return (
    <div className="prompt" role="presentation">
      <section
        className="prompt-card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="project-changed-title"
        aria-describedby="project-changed-message"
        data-testid="project-changed"
      >
        <h2 id="project-changed-title">Project changed on disk</h2>
        <p id="project-changed-message">{message}</p>
        <p className="hint">
          Reload opens the file from disk and clears undo history. Overwrite saves the project shown here over it.
        </p>
        <div className="prompt-actions">
          <button type="button" data-testid="prompt-cancel" autoFocus onClick={onCancel}>
            Cancel
          </button>
          <button type="button" data-testid="prompt-overwrite" onClick={onOverwrite}>
            Overwrite
          </button>
          <button type="button" data-testid="prompt-reload" onClick={onReload}>
            Reload
          </button>
        </div>
      </section>
    </div>
  );
}
