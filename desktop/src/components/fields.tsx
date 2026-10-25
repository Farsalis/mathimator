import { useEffect, useEffectEvent, useRef } from "react";
import { parseDecimal } from "../format";
import { useCommitDraft, type CommitDraft } from "../useCommitDraft";

/**
 * A colour picker beside a hex field. While the picker is open it moves only the draft; it commits once, on the
 * native `change` event when it closes, so one use of it is one edit and one undo step (D08, F009). React's onChange
 * on a colour input follows every `input` event, hence the listener. The hex field commits on blur. Both compare
 * case-insensitively.
 */
export function ColorField({
  label,
  value,
  testId,
  onCommit,
}: {
  label: string;
  value: string;
  testId?: string;
  onCommit: (color: string) => Promise<void>;
}) {
  const draft = useCommitDraft(value);
  const picker = useRef<HTMLInputElement>(null);

  function commitColor(color: string): void {
    if (color.toUpperCase() === value.toUpperCase()) {
      draft.revert();
    } else {
      draft.commit(onCommit(color));
    }
  }

  const onPickerClosed = useEffectEvent((color: string) => commitColor(color.toUpperCase()));

  useEffect(() => {
    const element = picker.current;
    if (!element) {
      return;
    }
    const listener = (): void => onPickerClosed(element.value);
    element.addEventListener("change", listener);
    return () => element.removeEventListener("change", listener);
  }, []);

  return (
    <label>
      {label}
      <span className="color-row">
        <input
          ref={picker}
          type="color"
          value={toColorInput(draft.text)}
          onChange={(event) => draft.change(event.target.value.toUpperCase())}
        />
        <input
          data-testid={testId}
          value={draft.text}
          onChange={(event) => draft.change(event.target.value)}
          onBlur={() => commitColor(draft.text)}
        />
      </span>
    </label>
  );
}

interface TextFieldProps {
  label: string;
  value: string;
  testId?: string;
  onCommit: (value: string) => Promise<void>;
}

/** A text field that commits on blur or Enter. */
export function CommitText(props: TextFieldProps) {
  const draft = useCommitDraft(props.value);
  return <DraftText {...props} draft={draft} />;
}

/** CommitText over a draft its parent holds, for a parent that shows the draft too, as the formula preview does. */
export function DraftText({ label, value, testId, draft, onCommit }: TextFieldProps & { draft: CommitDraft }) {
  return (
    <label>
      {label}
      <input
        data-testid={testId}
        value={draft.text}
        onChange={(event) => draft.change(event.target.value)}
        onBlur={() => {
          if (draft.text !== value) {
            draft.commit(onCommit(draft.text));
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
        }}
      />
    </label>
  );
}

/** A number field that commits on blur or Enter. A cleared field, or text that is not a decimal, shows the value again. */
export function CommitNumber({
  label,
  value,
  testId,
  onCommit,
}: {
  label: string;
  value: number;
  testId?: string;
  onCommit: (value: number) => Promise<void>;
}) {
  const draft = useCommitDraft(String(value));
  return (
    <label>
      {label}
      <input
        data-testid={testId}
        inputMode="decimal"
        value={draft.text}
        onChange={(event) => draft.change(event.target.value)}
        onBlur={() => {
          const next = parseDecimal(draft.text);
          if (next === null || next === value) {
            draft.revert();
          } else {
            draft.commit(onCommit(next));
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
        }}
      />
    </label>
  );
}

function toColorInput(value: string): string {
  return /^#[0-9A-Fa-f]{6}$/.test(value) ? value.toLowerCase() : "#ffffff";
}
