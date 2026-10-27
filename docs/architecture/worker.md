# The worker

The worker is the Python process that owns the document and runs the renders.
The shell starts it as

```text
<python> -P -m mathimator_worker --project <folder> --python <python> [--init] [--log <file>]
```

and talks to it in JSON lines over stdin and stdout ([the protocol](protocol.md)).
It is also called the supervisor, because it supervises one Manim process at a
time. Its package is [`worker/mathimator_worker`](../../worker/mathimator_worker).

The worker imports only the standard library. It never imports Manim or PyAV:
[`__main__.py`](../../worker/mathimator_worker/__main__.py) exits with status 2
if either is loaded, and the tests check it. Manim runs in a separate process
that the worker starts for each render.

## Module map

| Module | Role |
| --- | --- |
| [`__main__.py`](../../worker/mathimator_worker/__main__.py) | Command line, UTF-8 stdio, the forbidden-import check, then `Supervisor(...).serve()` |
| [`supervisor.py`](../../worker/mathimator_worker/supervisor.py) | The request loop, the project binding, the job queue and every job's run |
| [`protocol.py`](../../worker/mathimator_worker/protocol.py) | `PROTOCOL_VERSION`, the request envelope check and the reply builders |
| [`session.py`](../../worker/mathimator_worker/session.py) | The open document, undo and redo, every edit operation, dirty flags and the `state` reply |
| [`document.py`](../../worker/mathimator_worker/document.py) | The `project.json` model, parsing, validation, the limits and the export formats |
| [`document_error.py`](../../worker/mathimator_worker/document_error.py) | `DocumentError`, a refusal with a protocol code and a field |
| [`jsonio.py`](../../worker/mathimator_worker/jsonio.py) | Strict JSON reading, the stable saved form, the canonical hash form and cache records |
| [`expr.py`](../../worker/mathimator_worker/expr.py) | The expression grammar for plots, surfaces and curves, and its translation to numpy |
| [`latex.py`](../../worker/mathimator_worker/latex.py) | The allowlist of LaTeX commands a formula may use |
| [`beats.py`](../../worker/mathimator_worker/beats.py) | The beat catalog, the easings and the shapes: one table every reader shares |
| [`timeline.py`](../../worker/mathimator_worker/timeline.py) | The on-screen rules: what is on screen before each beat, and the camera in effect |
| [`templates.py`](../../worker/mathimator_worker/templates.py) | Inserting the templates, a formula, a text or a shape |
| [`codegen.py`](../../worker/mathimator_worker/codegen.py) | The generated `scene.py` |
| [`identity.py`](../../worker/mathimator_worker/identity.py) | Cache identities: beat keys, chain keys, the scene stamp, `sceneHash` and `exportKey` |
| [`layout.py`](../../worker/mathimator_worker/layout.py) | Where a project keeps its files, and where Manim writes inside them |
| [`project_folder.py`](../../worker/mathimator_worker/project_folder.py) | The project lock, folder identity, atomic writes, link checks and deletes that never follow a link |
| [`argv.py`](../../worker/mathimator_worker/argv.py) | Manim's command line and environment |
| [`manim_paths.py`](../../worker/mathimator_worker/manim_paths.py) | The project path check before a render, and the 8.3 alias on Windows |
| [`manim_main.py`](../../worker/mathimator_worker/manim_main.py) | Runs Manim's own command line with two fixes to its scene writer; it runs inside the Manim process |
| [`manim.cfg`](../../worker/mathimator_worker/manim.cfg) | Mathimator's Manim settings, passed to every render |
| [`progress.py`](../../worker/mathimator_worker/progress.py) | Follows a render through the files Manim writes |
| [`clip_check.py`](../../worker/mathimator_worker/clip_check.py) | Tells a complete MP4, MOV or WebM from a cut-off one |
| [`preview_index.py`](../../worker/mathimator_worker/preview_index.py) | Each beat's preview files and whether they are fresh |
| [`exports.py`](../../worker/mathimator_worker/exports.py) | Finding, checking and publishing a finished export |
| [`tex_failure.py`](../../worker/mathimator_worker/tex_failure.py) | The message of a failed render, with TeX's own error lines |
| [`win32_job.py`](../../worker/mathimator_worker/win32_job.py) | Kill-on-close Job Objects through `ctypes` |
| [`process_group.py`](../../worker/mathimator_worker/process_group.py) | Ending a process tree when no Job Object does |
| [`equation.py`](../../worker/mathimator_worker/equation.py) | The reference LaTeX-to-Typst translator that the shell's port is tested against |

## The supervisor

[`Supervisor`](../../worker/mathimator_worker/supervisor.py) is built once per
process. Its constructor binds the startup folder and its `serve` method reads
stdin line by line until it closes, then stops any running job and releases
the project lock.

`handle_line` parses each line with `protocol.parse_request` and calls the
handler the command table names. A malformed line is answered with
`error bad_request`. Any other exception is logged with its traceback and
answered with `error internal`; the worker keeps serving with its undo history
and any running export. Because every handler changes state all or nothing,
nothing is left half changed.

### Threads and locks

| Thread | Does |
| --- | --- |
| Main | Reads requests and answers them, one at a time |
| One per job | Prepares the job, runs Manim, reports progress and writes the terminal line |
| Two per Manim run | Drain Manim's stdout and stderr into `worker.log`, so Manim never blocks on a full pipe |
| Process table (Windows, rare) | Starts the PowerShell helper of the cancel fallback, beside the first render that runs without a Job Object |

| Lock | Guards |
| --- | --- |
| `_state_lock` | The running job and the two hold slots, and each job's process and Job Object handle |
| `_doc_lock` | The session, the binding, the preview index and the TeX error |
| `_stdout_lock` | One line at a time on stdout |
| `_log_lock` | Appending to and rotating `worker.log` |

The document lock is taken before the stdout lock. A job records a finished
beat in the preview index and writes its `beat_ready` line under the document
lock, so an edit, undo, reload or bind either commits first, and the line is
not sent, or comes after, and its reply follows the line on stdout.

### Binding a project

The worker is bound to at most one project folder. A bind happens at startup,
on `open_project` (Open, Sample and New) and on `save_as`. While bound, the
worker holds:

- **The project lock**, an exclusive OS lock on `.mathimator/lock`
  ([`ProjectLock`](../../worker/mathimator_worker/project_folder.py):
  `msvcrt.locking` on Windows, `flock` elsewhere). A second window that opens
  the folder is refused with `project_locked`. The lock ends with the process,
  so a crash leaves none behind. On Windows the open lock file also stops the
  folder from being renamed or deleted while it is open.
- **The folder identity**, `(st_dev, st_ino)`. A folder that was moved,
  deleted or replaced by another one at the same path no longer matches.
- **The fingerprint**, the SHA-256 of `project.json` as last read or written.

A bind goes through `_claim`, which checks that none of the project's own
folders is a link, takes the lock (kept when the same folder is opened again),
writes an empty project when `--init` or `init` asks for one, and reads and
validates `project.json`. Anything it created is removed if a later step
refuses. Then `_bind_locked` switches the layout, binding and session in one
step: it sweeps what an interrupted render left behind, loads
`cache/rendered.json` for a new session, reads and rebuilds the preview index,
writes the records back, bumps the binding generation and releases the old
lock. A job that started under an earlier generation records nothing in the
folder bound now.

Startup never exits on a project problem. A missing folder, a link, a lock
held elsewhere or a refused `project.json` leaves the worker unbound and
serving, with the refusal in `ready.startupError`. A folder without
`project.json`, started without `--init`, is in hand-written mode: it renders
the `scene.py` found in `.mathimator/codegen/` and has no document. Only the
tests use that mode.

### Write and render checks

Before a request writes `project.json` or starts a render, the worker checks,
in order, that the folder still has the identity it was bound with
(`project_unavailable`), that `project.json` exists (`project_unavailable`),
that it can be read (`read_failed`), and that its bytes still hash to the
fingerprint (`project_changed`). So the worker never overwrites a file someone
else changed, never recreates a deleted folder, and never writes an empty
document over a missing one. The UI turns `project_changed` into a prompt
with Reload, Overwrite and Cancel.

`reload` reads `project.json` again into a new session, which clears the undo
history. `overwrite` writes the document in memory without the fingerprint
check. `save_as` locks a target folder that holds neither `project.json` nor
`.mathimator`, writes `project.json`, copies the media, last-good files and
exports, writes fresh records that name the copies, and binds the target with
the same session and undo history. It never
changes the old folder, and removes what it wrote in the target if anything
fails.

`clear_cache` deletes the derived folders (`cache`, `codegen`, `equation` and
the `typst` folder of older builds) and every log but the active one, which it
empties. It keeps `project.json`, `exports/`, the lock and the temp-project
marker, and reports files another program holds with a `files_in_use` warning.
It is refused while a job runs or waits.

## Session, edits and undo

A [`Session`](../../worker/mathimator_worker/session.py) holds the open
document, the undo and redo stacks, and the render records. The stacks hold
whole documents, up to 100 undo steps, and live only in memory.

An edit goes through `Session.edited`:

1. Copy the document by saving and reading it back.
2. Apply the edit to the copy. `_EDITS` maps each `op` to a function, such as
   `_set_latex` or `_add_beat`. A function raises `DocumentError` naming the
   edit field at fault, or returns an `EditResult` with the beat and object
   the UI should select.
3. Save the edited copy and read it back through the full validator, so an edit
   is checked against every document rule, not only its own.
4. Refuse the edit if reading it back changed a value. Loading rewrites every
   field a template owns from the template, so such a change would otherwise be
   accepted and silently lost. The refusal is `invalid_edit`, naming the JSON
   path.

The supervisor's `_edit` then compares the saved bytes of the old and new
documents. If nothing changed it answers `state {changed: false}`, writes
nothing and keeps Redo. Otherwise it runs the write checks, writes
`project.json` atomically, and only then commits one undo step and clears
Redo. Undo and redo save the target document first and move between the stacks
only once the save succeeded.

`dirty_flags` compares each beat's cache key with the keys recorded when the
last full preview finished (`rendered.json`). Every beat from the first
mismatch on is dirty, and all of them are when the scene stamp changed.
`state_event` builds the `state` reply: the document, the derived start times,
total, dirty flags, `canUndo` and `canRedo`, the on-screen sets, the preview
index, and the generated source (or `sourceError`).

## The document model and validation

[`document.py`](../../worker/mathimator_worker/document.py) holds the model as
dataclasses (`Project`, `Scene`, `SceneObject`, `Beat`, `TemplateInstance`,
`Camera` and the two profiles) and the limits every module shares, such as
`MIN_BEAT_SECONDS`, `MAX_TOTAL_SECONDS`, `GENERATOR_VERSION` and the export
`FORMATS`.

Reading `project.json` (`load_bytes`, then `load_text`) goes through these
steps, and the first failure is a `DocumentError` whose message and `field`
name the JSON path of the value, such as `scene.objects[2].params.latex`:

1. **Encoding.** One leading UTF-8 byte order mark is dropped. Any other
   encoding, including UTF-16, is refused with `not_utf8`.
2. **Strict JSON.** `jsonio.loads` refuses `NaN`, `Infinity`, numbers too
   large for a float, and nesting that is too deep (`invalid_json`).
3. **Schema.** `schemaVersion` must be the integer 1. An older
   `generatorVersion` is read as the current one, so the next save writes the
   current number; a newer one is refused. Unknown keys, a stored `start`, an
   unknown object type and bad ids are refused.
4. **Values.** Each object's params are parsed by type. Strings that hold
   characters that cannot be stored are refused (`invalid_text`). Formula LaTeX
   must pass [`latex.check_latex`](../../worker/mathimator_worker/latex.py)
   (`untrusted_latex`). Expressions must parse under
   [`expr.py`](../../worker/mathimator_worker/expr.py)'s grammar
   (`unsupported_expression`). Numbers are judged at the three decimals codegen
   writes, and limits such as 500 ticks per axis, the 24×24 surface cap and
   the 120 s total give `limit_exceeded`.
5. **Identity.** Ids are unique across the document, and no object id clashes
   with a name the generated code uses (`np`, `self`, `config`, an easing,
   `<axes>_labels`, `<axes>_group`, or a lambda parameter).
6. **Templates.** Each template instance rewrites the objects it owns from its
   params, so a hand edit to a generated object does not survive.
7. **Scene kind, timeline and templates.** A 3D scene holds only the 3D
   template's two objects, the beats must play out on screen
   ([`timeline.py`](#the-on-screen-timeline)), and the template links must hold.

Saving uses `dumps_project`: two-space indentation, stable key order,
uppercase colours and a trailing newline, so the same document always has the
same bytes. Hashing uses the separate canonical form of `jsonio.canonical_dumps`:
sorted keys, no whitespace and three decimal places.

### Templates

[`templates.py`](../../worker/mathimator_worker/templates.py) inserts the three
templates (`axes_function`, `formula_transform` and `three_d_axes`), a single
formula or text, and the six shapes, each with its entrance beats, after the
beat the edit names or at the end. A template instance records its params,
object ids and beat ids. Its params are the source of truth: an edit of a
template-owned field, such as a plot's expression, changes the instance's
params, and `document.py` rewrites the objects from them (`sync_three_d` and
the `_sync_*` functions). The 3D template needs an empty project and turns the
scene into a `three_d_scene` with a camera; switching it between surface and
curve replaces the second object and keeps both beat ids.

## The beat catalog

[`beats.py`](../../worker/mathimator_worker/beats.py) is data only. Every
reader takes its rules from it: parsing and validation, the timeline, codegen,
the edit defaults, and the UI, through its typed copy in
[`desktop/src/beatKinds.ts`](../../desktop/src/beatKinds.ts).
`uv run pytest tests/test_beats.py --update-golden` writes the table to
[`tests/fixtures/protocol/beat-kinds.json`](../../tests/fixtures/protocol/beat-kinds.json),
and the UI's tests check their copy against that file.

Each `BeatKind` row gives:

- its **family**, which says what the beat does to each target's on-screen
  state: `hold` (wait), `entrance`, `exit`, `change`, `emphasis`, `morph` or
  `camera`;
- its **arity**: no targets, one or more, or a pair with named roles (a morph's
  source and destination, or an object and the path it moves along);
- the object **types** it takes, its **settings** (`Param` rows with defaults
  and bounds), the **easings** it allows and its default easing;
- for codegen, the animation call per target (`call`, `centred_call`), whether
  targets act about a shared centre, whether they are saved and restored around
  the play, and whether Manim's rate function runs backwards (`reversed_rate`);
- `predictable`, which is false for a camera move: the state it leaves can
  change Manim's cache key for later plays, so progress waits for the segment
  list after it ([below](#progress-and-the-preview-index)).

`EASINGS` lists the nine Manim rate functions a beat may name, `SHAPES` the six
shape types with their params and entrance kind, and `NATURAL_ENTRANCE` the
entrance each object type gets when it is added.

## The on-screen timeline

[`timeline.simulate`](../../worker/mathimator_worker/timeline.py) plays the
beats out in order, with nothing on screen at the start, and refuses the first
beat that breaks a rule. For each beat it checks that the scene offers the
kind, the number of targets, that each target names an object and appears
once, the target types, and each target's state: an entrance needs its target
off screen, an exit, change or emphasis needs it on, and a morph takes its
source off and brings its destination on. An object drawn on axes (a plot, a
point on axes, a surface or a curve) is on screen only while its axes are.
After the last beat, every object must have entered at least once.

The result is a `Timeline`: the ids on screen before each beat and after the
last, which the `state` reply sends as `derived.onScreen`, and the camera in
effect before each beat. The UI uses `onScreen` to offer only the kinds and
targets that fit at a point in the timeline.

A broken rule about state is a `TimelineError` that also names the beat and the
object at fault. `delete_object` uses it to repair a timeline: it removes the
object (and what is drawn on it, and the 3D template's other object), drops
beats left with too few targets and templates that lost a part, then
repeatedly removes the target at fault from the first broken beat until the
beats play out. Each step only removes, so the repair always ends.

`canonical_targets` gives the order beat edits store targets in: document
order, with each axes moved before the first object drawn on it. A pair keeps
its role order. [`tests/fixtures/beats/canonical-order.json`](../../tests/fixtures/beats/canonical-order.json)
pins the rule for the worker and the UI's copy in
[`beatRules.ts`](../../desktop/src/beatRules.ts).

## Code generation

[`codegen.generate`](../../worker/mathimator_worker/codegen.py) turns a
document into `scene.py`. It is deterministic: the same document always gives
the same bytes, and a beat's code depends only on that beat, the objects it
names and its start and end time. That is what lets Manim reuse the partial
movies of unchanged beats. [Rendering and caching](rendering-and-caching.md)
describes the generated code and the frame rule. `needs_latex` tells whether a
scene runs LaTeX, which decides the TeX and path checks.

## The job queue

At most one Manim process runs. The supervisor keeps the running job
(`_current`) and two hold slots, one for an export and one for a preview:

| Request arrives while | `render_preview` | `export` |
| --- | --- | --- |
| Nothing runs | Starts | Starts |
| A preview runs | Cancels it; starts when it has stopped | Cancels the preview; starts when it has stopped |
| An export runs or waits | Waits in the preview slot, replacing an older waiting preview, which ends `error cancelled` | Refused with `export_running` |

`_supersede` stops the running preview and waits up to one second from when it
was first told to stop. If it is still alive then, the new job stays in its
slot and gets a `queued` line. When a job ends, `_finish_job` starts the held
export first, otherwise the held preview. Edits never cancel or start an
export.

`cancel {jobId}` kills a running job's Manim, or takes a held job out of its
slot and ends it with `error cancelled` at once. Any other id gives
`rejected no_such_job`. Open and Save As cancel previews the same way.

Each job writes exactly one terminal line, `done`, `error` or `rejected`,
through `_end`, which drops any later one. A cancelled job ends with
`error cancelled` from the `finally` of its thread.

### Running a job

`_run_job_inner` runs on the job's thread:

1. **Prepare, under the document lock** (`_prepare_job_locked`). The job takes
   the current layout and binding generation, runs the write and render
   checks, refuses links among the project's folders and the job's own output
   paths, generates the scene (`invalid_document`), checks TeX
   (`tex_unavailable`) and the project path (`path_unsupported`), and for an
   export without `force` looks in the export cache, which may end the job with
   `done` at once. It then deletes and recreates `.mathimator/codegen/` and
   writes `scene.py` there, so Manim imports nothing planted beside it. For a
   preview it rebuilds the index, copies the files of beats that are not fresh
   into `cache/last-good/`, and captures the chain keys of the document it
   renders. Last, it builds the argv. Starting a job never writes
   `project.json`.
2. **Clear stale media.** Manim's TeX folder is emptied of everything but the
   SVG files Manim reuses, the PNG-sequence folder is deleted before a PNG
   export, and incomplete partial movies are deleted so their beats render
   again.
3. **Write the run marker**, `cache/manim-run.json`: the quality folder, the
   format, and the size and modification time of every file in that folder.
4. **Run Manim** (`_run_manim`). The process starts hidden, in a new process
   group, with the environment of `argv.manim_environment`. On Windows it goes
   into a kill-on-close Job Object right away. The worker sends `started`, then
   waits, polling for finished beats every 0.1 s while a preview runs.
5. **Finish.** A cancelled or failed run deletes what it may have cut off
   (`_discard`). A failure becomes `render_failed` with a one-line message and
   the last 80 lines of Manim's output as details. A run that exits 0 is
   checked: every clip in its segment list must be complete, the output must
   exist, and a PNG sequence must have the expected number of frames. Then the
   preview is recorded or the export is published.
6. **Remove the run marker**, whatever happened.

If the worker quits, crashes or is killed mid-render, the run marker stays.
The next bind of that folder deletes what the run wrote, by the same
comparison of sizes and modification times, and then the marker.

## Progress and the preview index

The worker never parses Manim's console output for progress, because Rich
wraps it to its own width. [`ManimProgress`](../../worker/mathimator_worker/progress.py)
follows the files instead:

- Before Manim starts, it records the size and modification time of every file
  in the job's quality folder, such as `videos/scene/480p15`, and in its
  partials folder, `partial_movie_files/MainScene`. A file that is new or
  changed since then is one this run wrote. No clock is compared.
- Manim writes one partial movie per beat, one after another, and reuses an
  existing partial instead of writing it again. So the worker knows which beats
  Manim will render (the beats that are not fresh) and which it will reuse.
- A written partial counts as finished once a newer one exists, and only when
  [`clip_check`](../../worker/mathimator_worker/clip_check.py) finds it
  complete. It belongs to the next beat Manim renders. Beats go out in
  increasing order, so a finished beat waits until every beat before it is
  known.
- When the order cannot be trusted, the remaining beats wait for the segment
  list instead: two partials with the same modification time, an incomplete
  clip, more clips than expected, or any beat after the first camera move (see
  `predictable` above).
- When Manim exits 0, the segment list it wrote,
  `partial_movie_file_list.txt`, gives every beat's clip in order, including
  the ones it reused. Only the file names are read from it, and each is spelled
  inside this run's folder, so no path the worker sends comes from Manim.

The [`PreviewIndex`](../../worker/mathimator_worker/preview_index.py) holds,
for each beat, its id, derived start, duration, current partial, last good
file and whether it is fresh, plus the path of the full preview movie. Each
file carries the chain key of the document it was rendered from, so a file
counts as fresh only for that document. `rebuild_index` fits the index to the
current document after every change: a beat whose key matches one of its files
is fresh, otherwise its newest usable file becomes its last good file. The
full movie is kept only while every beat is fresh and it is the render of this
very document.

On disk, `preview-index.json` names its files relative to the project folder,
so a copied or moved project finds its own media, and a stored path outside the
project's media and last-good folders is dropped when the index is read. The
chain keys stay on disk; the `state` reply sends paths and the `fresh` flag
only.

A preview sends `beat_ready` only while it is not cancelled, belongs to the
folder bound now, and renders the document open now, beat for beat
(`_may_report_locked`).

## Exports

[`exports.py`](../../worker/mathimator_worker/exports.py) handles a finished
export:

- `find_export_output` finds what Manim wrote for the format:
  `MainScene.mp4` or `.webm`, `MainScene_ManimCE_v<version>.gif`, or the PNG
  folder `images/scene/MainScene`.
- `frame_count_problem` checks a PNG sequence: its frame count must equal the
  count Manim reports and the document's frame count at the export rate.
- `publish_export` refuses a file that is the preview (the preview-file guard),
  then copies the output into `.mathimator/exports/<key16>/` through a
  temporary name and writes `export.json` with the full export key. A failure
  leaves the older file and its manifest as they were and ends the job with
  `publish_failed`. The copy runs outside the document lock, so edits are
  answered while it runs.
- `existing_export` returns the published export of a document when its
  `export.json` records the current key, which answers a repeat export at once.

`done.current` says whether the key captured when the export started is still
the current document's, so the UI can say the file is from an older export.

## Process control

Cancel must end Manim and the `latex` and `dvisvgm` it started within a second,
and quitting the app must leave nothing running.

**Windows.** Each Manim process is assigned to a new Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` through `ctypes`
([`win32_job.py`](../../worker/mathimator_worker/win32_job.py)). Cancel calls
`TerminateJobObject`, and closing the handle after the run ends whatever Manim
left behind. The shell puts the worker itself in a kill-on-close job, so
quitting the app ends the whole tree.

When a debug run under a venv uses the venv's `python.exe`, which on Windows is
a launcher that starts the real interpreter as its child, the worker runs Manim
with the real interpreter instead and sets `__PYVENV_LAUNCHER__`
(`argv.manim_interpreter`). The process in the job is then Manim itself, and
everything it starts is born inside the job.

If Job Objects are off (`MATHIMATOR_WIN32_JOBS=0`) or assigning or terminating
one fails, [`process_group.py`](../../worker/mathimator_worker/process_group.py)
falls back to a hidden PowerShell helper, with a small C# class inside it that
lists every process with its parent and creation time and terminates by
handle. A process counts as a child only if it was created no earlier than its
parent, because Windows reuses process ids. The helper starts with the first
render that runs without a job, never at startup, and
`MATHIMATOR_SKIP_PROCESS_TABLE=1` keeps it from starting at all. The worker
does not use `pywin32` or `taskkill`.

**Linux.** Manim leads a new session. Cancel sends `SIGKILL` to its process
group and to any process that one scan of `/proc` finds below it. macOS has no
`/proc`, so only the group is killed there; macOS is untested.

## Manim's command line and environment

[`argv.build_manim_argv`](../../worker/mathimator_worker/argv.py) builds the
command line, with `-P` and `--check-hash-based-pycs always` for Python and
`--renderer cairo`, `--seed`, the size, frame rate and format, and
`--media_dir ../cache/media` for Manim. It refuses `--preview`,
`--live-preview`, `--disable_caching`, `--flush_cache`,
`--from_animation_number` and the OpenGL renderer.
[`manim_main.py`](../../worker/mathimator_worker/manim_main.py) is the module
the command runs: it patches two faults of Manim 0.22's scene writer (an
apostrophe in a path in FFmpeg's segment list, and a held frame of a wait that
an image sequence saved only once) and then runs `python -m manim`.

`argv.manim_environment` makes the codegen folder Manim's home (`HOME` and
`USERPROFILE`), so no per-user `manim.cfg` applies, sets `PYTHONHASHSEED=0` so
Manim's cache keys are the same in every process, and sets kpathsea's
`shell_escape=f`, `openin_any=p` and `openout_any=p` while dropping any
inherited variable that could override them.

[`manim_paths.manim_root`](../../worker/mathimator_worker/manim_paths.py)
checks the project path before a render. A `{` in the path cannot render. On
Windows, a path with characters outside the ANSI code page goes to Manim
through its 8.3 alias, which `dvisvgm` can read, and a path too long for
`dvisvgm` or, without Windows long paths, for Python is refused with
`path_unsupported`. Every path the worker reports stays in its long form.

## Failures and logs

[`tex_failure.format_render_failure`](../../worker/mathimator_worker/tex_failure.py)
writes the message of a failed render. A failure counts as a LaTeX failure
only when Manim's own TeX markers are in its output; the message is then
"LaTeX failed." with TeX's `!` line, its `<argument>` line and its `l.N` line,
read from the TeX log on disk. Any other failure is "manim exited with status
N: <exception>".

`worker.log` is in `.mathimator/logs/`, or the file `--log` names. It records
every command, every line the worker writes (`state` and `code` by size only),
Manim's working folder, command line, process id and output, and every error
with its traceback. It moves to `worker.log.1` at 5 MB. A line is never
written through a link, and a line the log cannot take goes to stderr, which
the shell keeps in `supervisor.err`.
