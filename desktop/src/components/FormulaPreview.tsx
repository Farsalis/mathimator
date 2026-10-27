import { useEffect, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { invokeShell } from "../commands";
import { applyGlyph, NO_GLYPH, parseGlyph, type GlyphView } from "../glyph";
import { errorMessage } from "../workerQueue";

const EQUATION_DEBOUNCE_MS = 100;
const CAPTION = "Live preview is Typst. The video uses LaTeX and will look different.";
const UNEXPECTED = "The equation preview returned an unexpected result.";

/**
 * The generation of the last compile any preview sent. One counter for the page, so a preview that mounts later
 * still asks above every generation the shell has seen compiled or cancelled.
 */
let generation = 0;

interface FormulaPreviewProps {
  /** The formula object's id. Two previews shown together compile in different slots. */
  slot: string;
  latex: string;
}

export function FormulaPreview({ slot, latex }: FormulaPreviewProps) {
  const [view, setView] = useState<GlyphView>(NO_GLYPH);
  /** The SVG read for `path`. It is shown only while `path` is still the view's. */
  const [svg, setSvg] = useState<{ path: string; url: string } | null>(null);
  const svgPath = view.svgPath;
  const blobUrl = svg !== null && svg.path === svgPath ? svg.url : null;

  useEffect(() => {
    let active = true;
    let request = 0;
    let inFlight = false;

    function compile(canRetry: boolean): void {
      generation += 1;
      request = generation;
      inFlight = true;
      void invokeShell("compile_equation_preview", { latex, slot, generation: request }).then(
        (value) => {
          inFlight = false;
          if (!active) {
            return;
          }
          const parsed = parseGlyph(value);
          if (parsed?.status === "cancelled" && canRetry) {
            // The shell saw a newer generation for this slot, for example before a page reload: ask once more.
            compile(false);
            return;
          }
          setView((shown) => (parsed ? applyGlyph(shown, parsed) : { ...shown, message: UNEXPECTED }));
        },
        (reason: unknown) => {
          inFlight = false;
          if (active) {
            setView((shown) => ({ ...shown, message: errorMessage(reason) }));
          }
        },
      );
    }

    const handle = window.setTimeout(() => compile(true), EQUATION_DEBOUNCE_MS);
    return () => {
      active = false;
      window.clearTimeout(handle);
      if (inFlight) {
        void invokeShell("cancel_equation_preview", { slot, generation: request });
      }
    };
  }, [latex, slot]);

  useEffect(() => {
    if (!svgPath) {
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    void fetch(convertFileSrc(svgPath))
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Could not load the equation preview (${response.status}).`);
        }
        return response.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) {
          return;
        }
        objectUrl = URL.createObjectURL(new Blob([buffer], { type: "image/svg+xml" }));
        setSvg({ path: svgPath, url: objectUrl });
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setView((shown) => ({ ...shown, message: errorMessage(reason) }));
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [svgPath]);

  return (
    <div className="equation-preview" data-testid="equation-preview">
      <p className="hint equation-caption" data-testid="equation-caption">
        {CAPTION}
      </p>
      {blobUrl ? <img className="equation-svg" data-testid="equation-svg" alt="" src={blobUrl} /> : null}
      {view.message ? (
        <p className="equation-message" data-testid="equation-message">
          {view.message}
        </p>
      ) : null}
    </div>
  );
}
