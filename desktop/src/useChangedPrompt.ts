import { useRef, useState } from "react";
import type { Task } from "./workerQueue";

export interface ChangedPrompt {
  /** The message of the open prompt, or null while none is open. */
  message: string | null;
  /** A `project_changed` refusal, with the queue paused: it joins the open prompt or opens one. */
  join: (message: string, retry: Task) => void;
  /** Closes the prompt and returns what Overwrite would run again, in the order the refusals came. */
  close: () => Task[];
}

/** The one `project_changed` prompt. Overwrite runs `retry` again for each refusal joined to it. */
export function useChangedPrompt(): ChangedPrompt {
  const [message, setMessage] = useState<string | null>(null);
  /** The retries of the refusals joined to the open prompt, or null while none is open. */
  const joined = useRef<Task[] | null>(null);

  return {
    message,
    join: (text, retry) => {
      if (joined.current) {
        joined.current.push(retry);
        return;
      }
      joined.current = [retry];
      setMessage(text);
    },
    close: () => {
      const retries = joined.current ?? [];
      joined.current = null;
      setMessage(null);
      return retries;
    },
  };
}
