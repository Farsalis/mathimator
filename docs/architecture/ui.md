# The UI

The UI is a React 19 app in TypeScript, built by Vite, in
[`desktop/src`](../../desktop/src). It runs in the Tauri webview and has no
backend of its own: the worker owns the document, and the UI shows the last
`state` reply it applied. The UI owns what is purely about the view: the
selection, unsaved field drafts, the playhead and playback, the open panel,
field errors, and which preview or export it is waiting for.

## File map

| File | Role |
| --- | --- |
| [`main.tsx`](../../desktop/src/main.tsx), [`App.tsx`](../../desktop/src/App.tsx) | The root: lays out the window and wires the components to `useEditor` |
| [`useEditor.ts`](../../desktop/src/useEditor.ts) | The editor hook: every piece of state and every action the window uses |
| [`workerQueue.ts`](../../desktop/src/workerQueue.ts) | The one ordered queue of worker tasks, and `invokeWorker` |
| [`commands.ts`](../../desktop/src/commands.ts) | The Tauri commands the UI calls, with their argument names, and `invokeShell` |
| [`jobSession.ts`](../../desktop/src/jobSession.ts) | The job handshake: pure functions over the preview and export the UI waits for and the status line |
| [`types.ts`](../../desktop/src/types.ts), [`parse.ts`](../../desktop/src/parse.ts) | The types of replies and events, and their checked parsers |
| [`edits.ts`](../../desktop/src/edits.ts) | One builder per edit operation |
| [`beatKinds.ts`](../../desktop/src/beatKinds.ts) | The UI's typed copy of the worker's beat catalog |
| [`beatRules.ts`](../../desktop/src/beatRules.ts) | Rules over the catalog: labels, target candidates, canonical order, + Beat |
| [`playback.ts`](../../desktop/src/playback.ts) | The scrub rule: which file and which time the canvas shows |
| [`format.ts`](../../desktop/src/format.ts) | Times, decimals, derived starts and the duration clamp |
| [`exportProfile.ts`](../../desktop/src/exportProfile.ts) | Export formats, sizes, frame rates and their transparency rules |
| [`glyph.ts`](../../desktop/src/glyph.ts) | The equation preview's result and view |
| [`useChangedPrompt.ts`](../../desktop/src/useChangedPrompt.ts), [`useCommitDraft.ts`](../../desktop/src/useCommitDraft.ts), [`useNotices.ts`](../../desktop/src/useNotices.ts) | Small hooks for the `project_changed` prompt, field drafts and notices |
| [`components/`](../../desktop/src/components) | The top bar, preview stage, inspector, beat editor, timeline, panels, banners and prompts |

Tests sit beside the modules they test, as `*.test.ts` or `*.test.tsx`.

## Talking to the shell

[`commands.ts`](../../desktop/src/commands.ts) lists every command the UI may
call, in two tables: `WORKER_COMMANDS`, which reach the worker, and
`SHELL_COMMANDS`, which the shell answers itself. Each entry names the
command's arguments, and the types make a call with other arguments a compile
error. The tables are the UI's half of
[`tests/fixtures/protocol/commands.json`](../../tests/fixtures/protocol/commands.json).

- `invokeShell(command, args)` calls a shell command and returns its value.
- `invokeWorker(command, args)` calls a worker-bound command and unwraps its
  reply. A `rejected` reply throws a `WorkerRefusal` with its code, message and
  field. An `error` reply, or a shell failure such as `timeout`, throws a
  `WorkerFailure`. A dialog's `{cancelled: true}` returns null. A rejection
  that is not a shell error gets the code `invoke_failed`.

Replies are parsed, not trusted. [`types.ts`](../../desktop/src/types.ts)
parses a `state` reply, an `AppStatus` and each `worker-event` with the
combinators of [`parse.ts`](../../desktop/src/parse.ts). A value of the wrong
shape throws a `ParseError` whose message names its path, such as
"Unexpected reply from the worker: state.derived.starts[1] must be a number."
An unknown event is ignored, and unknown fields are dropped, so a newer worker
within the same protocol does not break the UI.

## The worker queue

[`WorkerQueue`](../../desktop/src/workerQueue.ts) runs tasks one at a time, in
the order they were queued. A task is an async function that usually sends one
request and applies its reply. Because the next task starts only after the
previous one has applied its result, the UI applies replies in the order it
asked, and at most one worker request is outstanding.

- A task that fails is reported (banner or "Worker unavailable") and never
  blocks the tasks after it.
- A `timeout` failure also queues one `get_state` resync. At most one resync
  waits in the queue, and one that fails is reported, not repeated.
- A `project_changed` refusal pauses the queue and opens the prompt. Further
  `project_changed` refusals join it. Reload, Overwrite and Cancel resume the
  queue: Overwrite first saves this window's document, then retries each
  joined request once, in order.

Job events arrive outside the queue, on the `worker-event` channel.

## Startup and states

At mount, `useEditor` subscribes to `worker-event`, then calls `app_status`,
which answers once the shell's launch handshake is done. `applyStatus` shows
the shell's notices and TeX state, and either opens "Worker unavailable" with
the reason and Retry (`restart_worker`) or queues a `get_state`. Once the
worker is ready, the UI calls `note_shell_ready` once.

Every reply that carries a `state` goes through one of a few functions:

- `applyState`: the normal path. A state for a different `projectPath` than
  the last one, such as a late Open reply, first runs the Open reset.
- `applyEdit`: an edit reply. If it changed the document, the current preview
  becomes stale: its later `beat_ready` lines are ignored.
- `applyOpened`: Open, Sample and New. Runs the Open reset (no selection, no
  drafts, playhead at 0, playback stopped, View code and Notices closed, no
  field errors, export path or note), then schedules a preview.
- `applySavedAs`: Save As. The state names the new folder but holds the same
  document, so nothing is reset; the shown export path is moved to its copy in
  the new folder.

The selection is a beat and an object at once. `selectionIn` drops a selected
beat or object the document no longer holds, so the selection never names a
deleted item. An edit's `focus` selects what the worker names, such as a new
beat and its first target.

When the worker exits, the UI ends every job it waits for without showing
"Cancelled", resets the Export button, and resyncs with one `get_state`. If the
state is for the same folder, it clears drafts and field errors and shows "The
worker restarted. The document was reloaded from disk and undo history was
cleared."

## The job session

[`jobSession.ts`](../../desktop/src/jobSession.ts) is a small state machine
written as pure functions over a `Handshake` record: the current preview id and
whether it is stale, the current export id, whether an export runs and whether
the user cancelled it, whether the export pre-empted a preview, the status
line, the note under it, and the last export path.

The UI records a job's id before it sends the job, so no event can arrive for
an id it does not know. `receive(session, event)` applies one `worker-event`
and returns the new session with what to do next: a failure to show, a beat to
patch into the preview index, and a follow-up, which is one of `refresh`
(queue `get_state` after a preview's `done`), `preview` (schedule a preview
after an export that pre-empted one) or `resync` (after `worker_exited`).
Events for any other id are ignored.

The status line reads "Ready", "Rendering preview", "Rendering preview — beat
N", "Rendering export", "Waiting for the previous render to stop" or
"Cancelled", as the session dictates.

## Previews

`schedulePreview` debounces previews by 300 ms. Each call bumps an epoch, so a
later call supersedes a preview task that has not yet recorded its id. The
preview task reserves a job id with `reserve_job_id`, records it with
`beginPreview`, and calls `render_preview`.

A preview is scheduled after an edit that changed the document (except a
rename), after Open, Sample, New, Reload and Clear cache, and after an export
that pre-empted a preview. None is scheduled after undo or redo, for a scene
without beats, or after a resync. Manim reuses every unchanged beat, so a
preview after a small edit renders only the beats from the first changed one.

Each `beat_ready` of the current preview is patched into the applied state
(`patchBeat`), so the canvas can show that beat before the preview ends. The
preview's `done` queues a `get_state`, which brings the settled index.

## Exports

Export opens the [`ExportPanel`](../../desktop/src/components/ExportPanel.tsx),
where the user picks a preset (MP4, GIF, WebM or PNG sequence), a size, a
frame rate and, for WebM and PNG sequence only, transparency. The choices and
their messages come from [`exportProfile.ts`](../../desktop/src/exportProfile.ts),
which the tests check against
[`tests/fixtures/protocol/export-choices.json`](../../tests/fixtures/protocol/export-choices.json).

Export (or Re-export, which passes `force`) shows "Rendering export" at once,
disables New, Open, Sample and Save As, and queues the export task:

1. Reserve an export id and record it.
2. Unless Cancel export was already pressed, send `set_export_profile`, an
   ordinary edit and one undo step when the profile changed.
3. If Cancel export was pressed meanwhile, stop with "Cancelled".
4. Send `export_movie` with the id and `force`.

Cancel export queues a `cancel_render` for the export id, behind the export
task, so the cancel always follows the send. An export's `done` shows the
published path, and "This file is from an older export." when `current` is
false.

## Playback and scrub

[`playbackAt(index, time)`](../../desktop/src/playback.ts) is the one scrub
rule; the worker has none. It finds the beat under the playhead (the last beat
whose start is at or before it) and returns what the canvas shows:

- the full preview movie, when every beat is fresh;
- otherwise the beat's own partial movie when it is fresh, with the "Preview
  is out of date" badge if any other beat is not;
- otherwise the beat's last good clip, with the badge;
- otherwise a blank canvas with the badge.

The time it returns is the middle of the frame to show, because a seek to the
edge of a frame can show the frame before it. Frames are computed in whole
milliseconds with integer arithmetic, rounding a half frame up, exactly as the
generated scene's `_beat_time` does, so a time on the timeline and a frame in
the movie agree. [`tests/fixtures/timing/frame-boundaries.json`](../../tests/fixtures/timing/frame-boundaries.json)
holds cases both sides check. `playheadAt` is the inverse: while a file plays,
it maps the video's time back to the timeline, never past the beat's end.

[`PreviewStage`](../../desktop/src/components/PreviewStage.tsx) loads the
chosen file with `fetch(convertFileSrc(path))` over the asset protocol, plays
it from a `blob:` URL, and keeps the last file on screen while the next one
loads. While a file plays, the playhead follows the video; over a beat with no
file, a clock moves the playhead until the next file plays. A file that cannot
be read is logged to the console and counts as missing only for the index it
was read for, so the canvas falls back to the last good clip or a blank one and
tries again with the next state.

## The Inspector

The [`Inspector`](../../desktop/src/components/Inspector.tsx) shows the object
list with Delete object, the 3D camera and surface or curve controls in a 3D
scene, the beat editor for the selected beat, and the fields of the selected
object. For a formula transform's beat it shows the fields of both formulas.

Fields commit, they do not stream. The building blocks in
[`components/fields.tsx`](../../desktop/src/components/fields.tsx) are:

- `CommitText` and `CommitNumber`, which commit on blur or Enter. A number
  field accepts a dot or a comma as the decimal point; an empty field or text
  that is not a decimal shows the stored value again and sends nothing.
- `ColorField`, a colour picker beside a hex field. The picker commits once,
  when it closes, so one use is one edit and one undo step.
- `ParamField`, one control for a setting of a beat kind or a shape, from its
  catalog row: a number with its range as a hint, an x and y pair, or a colour.

[`useCommitDraft`](../../desktop/src/useCommitDraft.ts) keeps what the user
typed until it is committed, and drops it as soon as the document holds a
different value, after an undo for example.

A refused edit shows its message under the field it is about. `FieldError`
records the object or beat, the edit's `op`, `axis` and `key`, and the
refusal's `field`, so that inputs whose edits all name `value` (the axis
ranges, a shape's settings) can be told apart. A refusal of a beat edit shows
under the beat editor's control that sent it. Field errors go when the
selection changes, and on undo, redo and Open.

### The beat editor

The [`BeatEditor`](../../desktop/src/components/BeatEditor.tsx) is generated
from the catalog in [`beatKinds.ts`](../../desktop/src/beatKinds.ts) and the
rules in [`beatRules.ts`](../../desktop/src/beatRules.ts), using
`derived.onScreen` to know what is on screen before the beat:

- **Kind**: the kinds the scene offers, grouped by family, with each kind that
  no object could take at that point disabled. Choosing one sends
  `set_beat_action` with targets the UI picks (`targetsForKind`).
- **Targets**: a checklist for kinds of one or more objects, or one select per
  role for a pair (From and To, or Object and Path). Checking axes for a kind
  that takes children along also checks what is drawn on them. Each change
  sends `set_beat_targets`.
- **Duration and settings**: the duration, then one `ParamField` per setting,
  each sending `set_beat_param`.
- **Easing**: "Default (…)", which sends null, then the kind's other easings
  (`set_beat_easing`).
- **Move earlier, Move later and Delete beat** (`move_beat`, `delete_beat`).

The kind, the targets and Delete beat are disabled for a beat that a template
owns. The UI's catalog copy is checked against
[`tests/fixtures/protocol/beat-kinds.json`](../../tests/fixtures/protocol/beat-kinds.json),
and its canonical target order against
[`tests/fixtures/beats/canonical-order.json`](../../tests/fixtures/beats/canonical-order.json).

## + Beat, Add and shapes

**Add** ([`AddMenu`](../../desktop/src/components/AddMenu.tsx)) inserts a
template, a formula, a text or a shape after the selected beat, or at the end
when no beat is selected (`insertionIndex`). Point on axes is disabled until
the document has 2D axes; its axes are the selected axes, else the axes of the
selected plot or point, else the first axes on screen there, else the first
axes in the document (`pointAxesId`).

**+ Beat** sends `add_beat` with a kind and targets that `plusBeat` chooses
from what is on screen at the insertion point:

- no object selected: a wait, or a camera move in a 3D scene;
- a selected object that is off screen there: its natural entrance (write for
  a formula or a text, grow for a dot or point, grow arrow for an arrow, create
  for the rest);
- a selected object that is on screen: indicate, which for axes also takes the
  on-screen objects drawn on them.

The worker fills in the settings, and its `focus` selects the new beat.

Every edit the UI sends is built by a function in
[`edits.ts`](../../desktop/src/edits.ts). The tests check each builder against
the examples in [`tests/fixtures/protocol/edit-ops.json`](../../tests/fixtures/protocol/edit-ops.json),
which the worker also accepts, and that no other source file writes an edit by
hand.

## The timeline

The [`Timeline`](../../desktop/src/components/Timeline.tsx) shows each beat
with its derived start, a label from `beatLabel` (such as "Fade out · Axes,
Sine"), a dirty mark, and a duration handle. Dragging the handle changes a
*draft* duration at 48 pixels per second, clamped by `clampDuration` between
0.1 s and what the 120 s total leaves. While a drag is in progress the
timeline derives the starts and the total from the drafts, without asking the
worker. Releasing the pointer commits `set_beat_duration`; a cancelled drag
returns to the duration it started from and sends nothing.

## The equation preview

[`FormulaPreview`](../../desktop/src/components/FormulaPreview.tsx) shows the
Typst glyph of a formula while it is edited. After a 100 ms debounce it calls
`compile_equation_preview` with the LaTeX, the object id as the slot, and a
generation from one counter for the page. When the field changes again, it
cancels the request in flight. A `cancelled` result is asked for once more,
which covers a page reload that restarted the counter. The SVG is read over
the asset protocol. An error keeps the last good SVG and shows the message.
The caption "Live preview is Typst. The video uses LaTeX and will look
different." is always shown.

## Errors, notices and panels

- [`ErrorBanner`](../../desktop/src/components/ErrorBanner.tsx) shows a
  failure as one line, with Show details for Manim's output and Dismiss. A
  `project_unavailable` banner offers Save As…, and a preview that failed on an
  incomplete clip the worker removed offers Render again.
- [`ProjectChangedPrompt`](../../desktop/src/components/ProjectChangedPrompt.tsx)
  is the modal prompt for `project_changed`. The rest of the window is inert
  while it is open, and Escape is Cancel.
- [`WorkerUnavailable`](../../desktop/src/components/WorkerUnavailable.tsx)
  shows why the worker cannot run, with Retry.
- The shell's notices show under the status line until dismissed, and a
  failed TeX check shows a persistent warning there.
- [`CodeView`](../../desktop/src/components/CodeView.tsx) shows the `source`
  of the applied state, so it follows every edit, undo and redo without a
  request of its own. Export code asks the worker for the same source.
- [`NoticesView`](../../desktop/src/components/NoticesView.tsx) shows a short
  licence summary and the shipped `THIRD_PARTY_NOTICES.txt`.
