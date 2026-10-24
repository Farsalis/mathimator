import { useEffect, useState } from "react";
import { AddMenu, type AddKind } from "./AddMenu";

interface TopBarProps {
  name: string;
  ready: boolean;
  canUndo: boolean;
  canRedo: boolean;
  /** An export runs: the Export button says so, and Open and Sample are disabled (D22). */
  exporting: boolean;
  onRename: (name: string) => void;
  onOpen: () => void;
  onAdd: (kind: AddKind) => void;
  onUndo: () => void;
  onRedo: () => void;
  onExport: () => void;
  onViewCode: () => void;
  onNotices: () => void;
  onOpenSample: () => void;
  onClearCache: () => void;
}

export function TopBar({
  name,
  ready,
  canUndo,
  canRedo,
  exporting,
  onRename,
  onOpen,
  onAdd,
  onUndo,
  onRedo,
  onExport,
  onViewCode,
  onNotices,
  onOpenSample,
  onClearCache,
}: TopBarProps) {
  const [draft, setDraft] = useState(name);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- TODO(audit-ui): resync during render (F119)
    setDraft(name);
  }, [name]);

  function commitName(): void {
    const next = draft.trim();
    if (next !== "" && next !== name) {
      onRename(next);
    } else {
      setDraft(name);
    }
  }

  return (
    <header className="top">
      <input
        className="project-name"
        data-testid="project-name"
        aria-label="Project name"
        value={draft}
        disabled={!ready}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commitName}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
        }}
      />
      <div className="top-actions">
        <button type="button" data-testid="open-button" onClick={onOpen} disabled={!ready || exporting}>
          Open
        </button>
        <AddMenu disabled={!ready} onAdd={onAdd} />
        <button type="button" data-testid="undo-button" onClick={onUndo} disabled={!ready || !canUndo}>
          Undo
        </button>
        <button type="button" data-testid="redo-button" onClick={onRedo} disabled={!ready || !canRedo}>
          Redo
        </button>
        <button type="button" data-testid="export-button" onClick={onExport} disabled={!ready}>
          {exporting ? "Rendering export" : "Export"}
        </button>
        <button type="button" data-testid="view-code-button" onClick={onViewCode} disabled={!ready}>
          View code
        </button>
        <button type="button" data-testid="notices-button" onClick={onNotices}>
          Notices
        </button>
        <button type="button" data-testid="sample-button" onClick={onOpenSample} disabled={!ready || exporting}>
          Sample
        </button>
        <button type="button" data-testid="clear-cache-button" onClick={onClearCache} disabled={!ready}>
          Clear cache
        </button>
      </div>
    </header>
  );
}
