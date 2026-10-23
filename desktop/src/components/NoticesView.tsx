import { NOTICES_TEXT } from "../notices";

interface NoticesViewProps {
  onClose: () => void;
}

export function NoticesView({ onClose }: NoticesViewProps) {
  return (
    <section className="notices-view" data-testid="notices-view" aria-label="Third-party notices">
      <header>
        <h2>Third-party notices</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </header>
      <pre data-testid="notices-text">{NOTICES_TEXT}</pre>
    </section>
  );
}
