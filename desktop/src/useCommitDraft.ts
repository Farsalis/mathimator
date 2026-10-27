import { useState } from "react";

/** What the user typed, and the value it was typed over. */
interface Draft {
  base: string;
  text: string;
}

export interface CommitDraft {
  /** What the field shows: the draft, or the value when there is none. */
  text: string;
  /** Records what the user typed. */
  change: (text: string) => void;
  /**
   * Keeps the draft until the edit `pending` settles, then shows the value again. A refused edit left the value as
   * it was, so the field no longer shows text the document does not hold.
   */
  commit: (pending: Promise<void>) => void;
  /** Drops the draft and shows the value. */
  revert: () => void;
}

/**
 * The draft of a field that commits on blur. A draft belongs to the value it was typed over: once the document
 * holds another value, after an undo or the edit itself, the draft is dropped while rendering, not by an effect.
 */
export function useCommitDraft(value: string): CommitDraft {
  const [draft, setDraft] = useState<Draft | null>(null);
  if (draft !== null && draft.base !== value) {
    setDraft(null);
  }
  return {
    text: draft?.text ?? value,
    change: (text) => setDraft({ base: value, text }),
    commit: (pending) => {
      const committed = draft;
      void pending.finally(() => setDraft((current) => (current === committed ? null : current)));
    },
    revert: () => setDraft(null),
  };
}
