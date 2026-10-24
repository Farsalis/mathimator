import type { AddKind } from "./components/AddMenu";
import { CodeView } from "./components/CodeView";
import { ErrorBanner } from "./components/ErrorBanner";
import { ExportPanel } from "./components/ExportPanel";
import { Inspector } from "./components/Inspector";
import { NoticesView } from "./components/NoticesView";
import { PreviewStage } from "./components/PreviewStage";
import { ProjectChangedPrompt } from "./components/ProjectChangedPrompt";
import { Timeline } from "./components/Timeline";
import { TopBar } from "./components/TopBar";
import { WorkerUnavailable } from "./components/WorkerUnavailable";
import type { Edit } from "./types";
import { useEditor } from "./useEditor";

export function App() {
  const editor = useEditor();
  const doc = editor.state?.document ?? null;
  const beats = doc?.scene.beats ?? [];
  const objects = doc?.scene.objects ?? [];
  const dirty = editor.state?.derived.dirty ?? [];
  const selectedBeatId = editor.selection.kind === "beat" ? editor.selection.id : null;
  const selectedObjectId = editor.selection.kind === "object" ? editor.selection.id : null;
  const canSaveAs = editor.ready && !editor.exporting;

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

  function onEdit(edit: Edit): Promise<void> {
    const preview = edit.op !== "set_project_name" && edit.op !== "set_object_name";
    return editor.commit(edit, preview);
  }

  return (
    <>
      <main className="app" inert={editor.changedPrompt !== null}>
        <TopBar
          name={doc?.name ?? "Untitled"}
          projectPath={editor.state?.projectPath ?? null}
          ready={editor.ready}
          canUndo={editor.state?.derived.canUndo === true}
          canRedo={editor.state?.derived.canRedo === true}
          exporting={editor.exporting}
          onRename={(name) => editor.commit({ op: "set_project_name", name }, false)}
          onNew={() => void editor.onNew()}
          onOpen={() => void editor.onOpen()}
          onSaveAs={() => void editor.onSaveAs()}
          onAdd={onAdd}
          onUndo={() => void editor.undo()}
          onRedo={() => void editor.redo()}
          onExport={editor.onExport}
          onViewCode={editor.onViewCode}
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
        {editor.texWarning ? (
          <p className="tex-warning" role="status" data-testid="tex-warning">
            {editor.texWarning}
          </p>
        ) : null}
        <div className="messages">
          {editor.unavailable !== null ? (
            <WorkerUnavailable reason={editor.unavailable} onRetry={editor.onRetry} />
          ) : null}
          {editor.error ? (
            <ErrorBanner
              failure={editor.error}
              canSaveAs={canSaveAs}
              onSaveAs={() => void editor.onSaveAs()}
              canRenderAgain={editor.ready}
              onRenderAgain={editor.onRenderAgain}
              onDismiss={editor.onDismissError}
            />
          ) : null}
          {editor.notices.map((notice) => (
            <div key={notice.id} className="notice" data-testid="notice">
              <p>{notice.message}</p>
              <button type="button" onClick={() => editor.onDismissNotice(notice.id)}>
                Dismiss
              </button>
            </div>
          ))}
          {editor.note ? (
            <p className="note" data-testid="export-note">
              {editor.note}
            </p>
          ) : null}
        </div>
        <PreviewStage
          index={editor.state?.index ?? null}
          time={editor.playhead}
          total={editor.total}
          playing={editor.playing}
          onTime={editor.setPlayhead}
          onPlaying={editor.setPlaying}
        />
        {editor.panel === "code" ? (
          <CodeView
            source={editor.state?.source ?? null}
            sourceError={editor.state?.sourceError}
            onClose={editor.onClosePanel}
            onExport={() => void editor.onExportCode()}
          />
        ) : editor.panel === "notices" ? (
          <NoticesView onClose={editor.onClosePanel} />
        ) : (
          <Inspector
            scene={doc?.scene ?? null}
            objects={objects}
            beats={beats}
            selectedBeatId={selectedBeatId}
            selectedObjectId={selectedObjectId}
            fieldError={editor.fieldError}
            texBlocked={editor.texWarning !== null}
            disabled={!editor.ready}
            onSelectObject={(objectId) => editor.select({ kind: "object", id: objectId })}
            onEdit={onEdit}
          />
        )}
        <Timeline
          beats={beats}
          durations={editor.durations}
          starts={editor.starts}
          dirty={dirty}
          selectedBeatId={selectedBeatId}
          playhead={editor.playhead}
          total={editor.total}
          disabled={!editor.ready}
          onSelect={(beatId) => editor.select({ kind: "beat", id: beatId })}
          onDraft={editor.onDraftDuration}
          onCommit={editor.onCommitDuration}
        />
        {editor.exportPath ? <p className="export-path">Export: {editor.exportPath}</p> : null}
        {editor.presetOpen && doc?.exportProfile ? (
          <ExportPanel
            initial={doc.exportProfile}
            canStart={editor.ready && beats.length > 0 && !editor.exporting}
            onStart={editor.onStartExport}
            onClose={() => editor.setPresetOpen(false)}
          />
        ) : null}
      </main>
      {editor.changedPrompt !== null ? (
        <ProjectChangedPrompt
          message={editor.changedPrompt}
          onReload={editor.onReload}
          onOverwrite={editor.onOverwrite}
          onCancel={editor.onCancelPrompt}
        />
      ) : null}
    </>
  );
}
