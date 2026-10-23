interface CodeViewProps {
  source: string;
  onClose: () => void;
  onExport: () => void;
}

export function CodeView({ source, onClose, onExport }: CodeViewProps) {
  return (
    <section className="code-view" data-testid="code-view" aria-label="Generated code">
      <header>
        <h2>Generated code</h2>
        <button type="button" data-testid="export-code-button" onClick={onExport}>
          Export code
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </header>
      <pre>
        <code>{source}</code>
      </pre>
    </section>
  );
}
