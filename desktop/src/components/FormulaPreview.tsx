import { useEffect, useRef, useState } from "react";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";

const EQUATION_DEBOUNCE_MS = 100;
const CAPTION = "Live preview is Typst. The video uses LaTeX and will look different.";
const UNSUPPORTED = "No live preview for this formula.";

type GlyphResult =
  | { status: "ok"; svgPath: string }
  | { status: "unsupported" }
  | { status: "error"; message: string }
  | { status: "cancelled" };

interface FormulaPreviewProps {
  latex: string;
}

export function FormulaPreview({ latex }: FormulaPreviewProps) {
  const [svgPath, setSvgPath] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const token = useRef(0);
  const compileStarted = useRef(false);

  useEffect(() => {
    const request = token.current + 1;
    token.current = request;
    compileStarted.current = false;
    const handle = window.setTimeout(() => {
      compileStarted.current = true;
      void invoke<unknown>("compile_equation_preview", { latex, generation: request })
        .then((value) => {
          if (token.current !== request) {
            return;
          }
          const parsed = parseGlyph(value);
          if (!parsed) {
            setMessage("The equation preview returned an unexpected result.");
            return;
          }
          applyGlyph(parsed, setSvgPath, setMessage);
        })
        .catch((reason: unknown) => {
          if (token.current !== request) {
            return;
          }
          setMessage(reason instanceof Error ? reason.message : String(reason));
        });
    }, EQUATION_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(handle);
      token.current += 1;
      if (compileStarted.current) {
        void invoke("cancel_equation_preview", { generation: request });
      }
    };
  }, [latex]);

  useEffect(() => {
    if (!svgPath) {
      setBlobUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    setBlobUrl(null);
    const src = convertFileSrc(svgPath);
    void fetch(src)
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
        setBlobUrl(objectUrl);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setMessage(reason instanceof Error ? reason.message : String(reason));
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
      {blobUrl ? (
        <img className="equation-svg" data-testid="equation-svg" alt="" src={blobUrl} />
      ) : null}
      {message ? (
        <p className="equation-message" data-testid="equation-message">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function applyGlyph(
  parsed: GlyphResult,
  setSvgPath: (path: string | null) => void,
  setMessage: (message: string | null) => void,
): void {
  switch (parsed.status) {
    case "ok":
      setSvgPath(parsed.svgPath);
      setMessage(null);
      return;
    case "unsupported":
      setSvgPath(null);
      setMessage(UNSUPPORTED);
      return;
    case "error":
      setMessage(parsed.message);
      return;
    case "cancelled":
      return;
    default: {
      const unexpected: never = parsed;
      throw new Error(`Unhandled glyph status ${JSON.stringify(unexpected)}`);
    }
  }
}

function parseGlyph(value: unknown): GlyphResult | null {
  if (!isRecord(value) || typeof value.status !== "string") {
    return null;
  }
  switch (value.status) {
    case "ok":
      return typeof value.svgPath === "string" ? { status: "ok", svgPath: value.svgPath } : null;
    case "unsupported":
      return { status: "unsupported" };
    case "error":
      return typeof value.message === "string" ? { status: "error", message: value.message } : null;
    case "cancelled":
      return { status: "cancelled" };
    default:
      return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
