import { useRef, useState } from "react";
import { invokeShell } from "./commands";
import type { Notice } from "./types";

export interface Notices {
  notices: Notice[];
  /** Shows the notices whose ids were never shown, so a StrictMode double mount shows each once (C9.5). */
  add: (added: readonly Notice[]) => void;
  /** Hides a notice and tells the shell, which then leaves it out of `app_status` (C9.5). */
  dismiss: (id: number) => void;
}

/** The shell's notices under the status line: each id is shown once (C9.5). */
export function useNotices(): Notices {
  const [notices, setNotices] = useState<Notice[]>([]);
  /** The id of every notice shown so far. */
  const shown = useRef(new Set<number>());

  return {
    notices,
    add: (added) => {
      const fresh = added.filter((notice) => !shown.current.has(notice.id));
      for (const notice of fresh) {
        shown.current.add(notice.id);
      }
      if (fresh.length > 0) {
        setNotices((current) => [...current, ...fresh]);
      }
    },
    dismiss: (id) => {
      setNotices((current) => current.filter((notice) => notice.id !== id));
      void invokeShell("dismiss_notice", { id });
    },
  };
}
