import type { AddKind } from "./components/AddMenu";
import { CodeView } from "./components/CodeView";
import { ExportPanel } from "./components/ExportPanel";
import { Inspector } from "./components/Inspector";
import { NoticesView } from "./components/NoticesView";
import { PreviewStage } from "./components/PreviewStage";
import { Timeline } from "./components/Timeline";
import { TopBar } from "./components/TopBar";
import type { Edit } from "./types";
import { useEditor } from "./useEditor";

export function App() {
  const editor = useEditor();
  const document = editor.state?.document ?? null;
  const beats = document?.scene.beats ?? [];
  const objects = document?.scene.objects ?? [];
  const dirty = editor.state?.derived.dirty ?? [];
  const durations = beats.map((beat) => editor.drafts[beat.id] ?? beat.duration);
  const selectedBeatId = editor.selection.kind === "beat" ? editor.selection.id : null;
  const selectedObjectId = editor.selection.kind === "object" ? editor.selection.id : null;

  function onAdd(kind: AddKind): void {
    const afterBeatId = selectedBeatId;
    let edit: Edit;
    switch (kind) {
      case "axes_function":
        edit = { op: "add_axes_function", afterBeatId };
        break;
      case "formula":
        edit = { op: "add_formula", afterBeatId };
        break;
      case "text":
        edit = { op: "add_text", afterBeatId };
        break;
      case "formula_transform":
        edit = { op: "add_formula_transform", afterBeatId };
        break;
      case "three_d_axes":
        edit = { op: "add_three_d_axes", afterBeatId };
        break;
      default: {
        const unexpected: never = kind;
        throw new Error(`Unhandled add ${String(unexpected)}`);
      }
    }
    void editor.commit(edit, true);
  }

  function onEdit(edit: Edit): void {
    const preview = edit.op !== "set_project_name" && edit.op !== "set_object_name";
    void editor.commit(edit, preview);
  }

  return (
    <main className="app">
      <TopBar
        name={document?.name ?? "Untitled"}
        ready={editor.ready && document !== null}
        canUndo={editor.state?.derived.canUndo === true}
        canRedo={editor.state?.derived.canRedo === true}
        canExport={editor.ready}
        exportLabel={editor.exportLabel}
        onRename={(name) => void editor.commit({ op: "set_project_name", name }, false)}
        onOpen={() => void editor.onOpen()}
        onAdd={onAdd}
        onUndo={() => void editor.undo()}
        onRedo={() => void editor.redo()}
        onExport={editor.onExport}
        onViewCode={() => void editor.onViewCode()}
        onNotices={editor.onNotices}
        onOpenSample={() => void editor.onOpenSample()}
        onClearCache={() => void editor.onClearCache()}
      />
      <div className="status-row">
        <p className="status" data-testid="status">
          {editor.status}
        </p>
        {editor.exporting ? (
          <button type="button" data-testid="cancel-export" onClick={editor.onCancelExport}>
            Cancel export
          </button>
        ) : null}
      </div>
      {editor.error ? (
        <p className="error" role="alert" data-testid="error">
          {editor.error}
        </p>
      ) : null}
      {editor.exportNote ? (
        <p className="export-note" data-testid="export-note">
          {editor.exportNote}
        </p>
      ) : null}
      <PreviewStage
        index={editor.state?.index ?? null}
        time={editor.playhead}
        total={editor.total}
        playing={editor.playing}
        onTime={editor.setPlayhead}
        onPlaying={editor.setPlaying}
      />
      {editor.codeOpen ? (
        <CodeView source={editor.codeText} onClose={() => editor.setCodeOpen(false)} onExport={() => void editor.onExportCode()} />
      ) : editor.noticesOpen ? (
        <NoticesView onClose={() => editor.setNoticesOpen(false)} />
      ) : (
        <Inspector
          scene={document?.scene ?? null}
          objects={objects}
          beats={beats}
          selectedBeatId={selectedBeatId}
          selectedObjectId={selectedObjectId}
          fieldError={editor.fieldError}
          onSelectObject={(objectId) => editor.setSelection({ kind: "object", id: objectId })}
          onEdit={onEdit}
        />
      )}
      <Timeline
        beats={beats}
        durations={durations}
        starts={editor.starts}
        dirty={dirty}
        selectedBeatId={selectedBeatId}
        playhead={editor.playhead}
        total={editor.total}
        onSelect={(beatId) => editor.setSelection({ kind: "beat", id: beatId })}
        onDraft={editor.onDraftDuration}
        onCommit={editor.onCommitDuration}
      />
      {editor.exportPath ? <p className="export-path">Export: {editor.exportPath}</p> : null}
      {editor.presetOpen && document?.exportProfile ? (
        <ExportPanel
          initial={document.exportProfile}
          canStart={editor.ready && beats.length > 0 && !editor.exporting}
          onStart={editor.onStartExport}
          onClose={() => editor.setPresetOpen(false)}
        />
      ) : null}
    </main>
  );
}
