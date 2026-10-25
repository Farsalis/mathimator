interface CodeViewProps {
  /** The generated scene.py of the applied state, or null when codegen refused the document. */
  source: string | null;
  /** Why there is no source. */
  sourceError?: string;
  onClose: () => void;
  onExport: () => void;
}

/** View code (D25): read-only, and it follows every applied state. */
export function CodeView({ source, sourceError, onClose, onExport }: CodeViewProps) {
  return (
    <section className="code-view" data-testid="code-view" aria-label="Generated code">
      <header>
        <h2>Generated code</h2>
        <button type="button" data-testid="export-code-button" disabled={source === null} onClick={onExport}>
          Export code
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </header>
      {source === null ? (
        <p className="field-error" data-testid="code-error">
          {sourceError}
        </p>
      ) : (
        <pre>
          <code data-testid="code-source">{source}</code>
        </pre>
      )}
    </section>
  );
}
