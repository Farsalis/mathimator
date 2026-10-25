import { useEffect, useState } from "react";
import { invokeShell } from "../commands";
import { asString } from "../parse";
import { errorMessage } from "../workerQueue";

/** The shipped THIRD_PARTY_NOTICES.txt, or why the shell could not read it. */
type NoticesFile = { text: string } | { error: string };

interface NoticesViewProps {
  onClose: () => void;
}

/** The Notices panel: a short summary, then the notices file the installer ships (D75). */
export function NoticesView({ onClose }: NoticesViewProps) {
  const [file, setFile] = useState<NoticesFile | null>(null);

  useEffect(() => {
    let active = true;
    void invokeShell("third_party_notices")
      .then((value): NoticesFile => ({ text: asString(value, "third_party_notices") }))
      .catch((reason: unknown): NoticesFile => ({ error: errorMessage(reason) }))
      .then((read) => {
        if (active) {
          setFile(read);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="notices-view" data-testid="notices-view" aria-label="Third-party notices">
      <header>
        <h2>Third-party notices</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="notices-summary" data-testid="notices-summary">
        <p>
          Mathimator's own source is under the MIT licence. The installer also ships third-party software, each part
          under its own licence and none of it relicensed under MIT. The main parts:
        </p>
        <ul>
          <li>CPython (Python Software Foundation License).</li>
          <li>Manim Community Edition (MIT) and the Python packages it uses.</li>
          <li>
            PyAV, which ships FFmpeg's libraries (LGPL-3.0-or-later). They include the x264 and x265 video encoders,
            under GPL-2.0-or-later, and the LAME MP3 encoder (LGPL-2.0-or-later).
          </li>
          <li>pycairo (LGPL-2.1 or MPL-1.1).</li>
          <li>
            Typst (Apache-2.0). Its LICENSE and NOTICE are in <code>runtime\typst</code> in the install folder.
          </li>
          <li>TinyTeX, whose TeX programs keep their own licences.</li>
          <li>The Microsoft Edge WebView2 Runtime, which draws this window: Microsoft software under its own terms.</li>
        </ul>
        <p>
          The licence texts that ship with these parts, and the written offer of the source code of the GPL and LGPL
          libraries, are in <code>runtime\THIRD_PARTY_NOTICES.txt</code> in the install folder, shown below.
        </p>
      </div>
      {file === null ? (
        <p className="hint">Reading the notices…</p>
      ) : "error" in file ? (
        <p className="field-error" data-testid="notices-error">
          {file.error}
        </p>
      ) : (
        <pre className="notices-file" tabIndex={0} aria-label="THIRD_PARTY_NOTICES.txt" data-testid="notices-text">
          {file.text}
        </pre>
      )}
    </section>
  );
}
