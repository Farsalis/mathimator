# Mathimator v1 design spec

Status: for approval. Document date: 21 October 2026. Revised 24 October 2026 for the audit decisions D01–D80, the owner's answers of 24 October 2026 and protocol 2.

Section 6 is the single source of the protocol between the shell and the worker, and `tests/fixtures/protocol/commands.json` is its machine-readable list of commands. README.md and CONTRIBUTING.md hold the build, test and release commands that section 8 summarizes.

Mathimator v1 is a Windows desktop app for one short math animation. A teacher or a YouTube explainer builds a visual document — objects plus an ordered timeline — and the app compiles that document into a Manim Community scene. The user exports a movie, a PNG sequence, or the generated Python. Making the animation does not require writing Python.

Approving this spec approves the timing budgets in section 2, the Typst-preview / LaTeX-final split in section 7, and MIT for Mathimator's own source. The other decisions in section 1 are already fixed.

## Terms

A **beat** is one step on the timeline. It compiles to exactly one `self.play(...)` or one `self.wait(...)`.

A **partial movie** is the video file Manim writes for a single `play` or `wait`. Manim reuses an unchanged partial movie. A changed call is rendered again, and so is every call after it. Earlier unchanged calls are reused.

A **preview profile** is the fixed on-canvas output: 854×480, 15 frames per second, opaque. An **export profile** is the format, resolution, frame rate, and transparency used for an export.

The **scene** is the objects, the beats, the background, the camera when the scene is 3D, and the seed.

## 1. Summary and decisions

v1 is one scene in one project, about 30 to 90 seconds, a handful of beats. The reference scene used for timing is 60 seconds and 8 beats. The editor refuses a document whose beat durations sum to more than 120 seconds.

The user adds a template or an object, edits parameters and beat durations, scrubs a preview, and exports. The generated Python is available from View code and from Export code. That view is read-only.

| Topic | Decision |
| --- | --- |
| Engine | Manim Community Edition, pinned to `manim==0.22.0`, Cairo renderer. The install extra is `manim[typst]==0.22.0`. v1 does not use ManimGL and does not track Manim's main branch. |
| Shell | Tauri 2.12.x on Windows. The UI is TypeScript. React + Vite is the toolkit inside that shell, an implementation choice, not a second product decision. |
| Worker | A Python sidecar supervises renders. Each render is a new grandchild process. On Windows the supervisor assigns that Manim process to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, using `ctypes`. Cancel terminates the job, which also reaps `latex` and `dvisvgm`. The shell assigns the supervisor to its own kill-on-close job, through the `windows-sys` bindings (D45), so quitting the app reaps the tree. Linux keeps the process-group path through Python's `subprocess` module. macOS is untested. The worker does not use `pywin32`, `win32api`, or `taskkill`. |
| Fallback shell | If phase 1 cannot open a Tauri window and host the sidecar, stop. Electron 45 is the documented alternative, and using it is a revision of this spec. |
| Platform | Windows 11 x64 only. The app is a desktop program. It is not a hosted website and not a PySide6 app. |
| Bundling | The installer ships one relocatable CPython 3.14.9 from python-build-standalone, not a virtual environment, so the installed app works on any account and runs Manim without a venv launcher (D33). CPython 3.13.17 is used only when a package in `uv.lock` has no wheel for 3.14.9. One interpreter for the whole runtime. Its render stack is the render group of `uv.lock`, `manim[typst]==0.22.0` and its locked dependencies, installed from the hashes the lock records (D34). The installer also ships TinyTeX (`latex` and `dvisvgm`), preseeded from a dated TeX Live snapshot with network installs disabled, and the Typst 0.15.0 binary. There is no separate FFmpeg program: Manim 0.19 and later encodes with PyAV, whose wheel ships FFmpeg's libraries. Section 8 says how the bundle is built and checked. |
| Installer | An offline NSIS installer that installs for all users under Program Files, which needs administrator rights (D62). It always embeds Microsoft's WebView2 offline installer, about 127 MB, and runs it silently on a PC that lacks the WebView2 runtime. A credible installer size before tuning is several hundred MB up to about 1 GB, plus the WebView2 payload. The Python runtime size is not yet measured. v1 installers are unsigned (D74). |
| First user | A teacher or a YouTube explainer. One scene of about 30–90 seconds. |
| Document | Objects and beats, compiled to a Manim scene. Export `.py`. No freeform Python editor. |
| License | MIT for Mathimator's own source, matching Manim Community, in `LICENSE`. Approving this spec confirms that license. The installer also ships third-party software under its own licenses, GPL and LGPL libraries among them: FFmpeg's libraries (LGPL-3.0-or-later) with the GPL-2.0-or-later x264 and x265 encoders and LAME, the GCC runtime, libiconv and pycairo. It ships their notices and a written offer of their source, and a legal review comes before the first public release (D75). |

The `manim[typst]` extra is installed because of the pin above. Scene code does not use the `Typst` or `TypstMath` mobjects that extra provides. Those mobjects render Typst to SVG and will not match Computer Modern or arbitrary LaTeX packages. The equation editor calls the Typst 0.15.0 binary for a live glyph. The scene and the export use `MathTex` and classic LaTeX.

Bundled programs keep their own licenses and run as separate programs: CPython with the native libraries it ships, such as OpenSSL, Tcl/Tk, libffi and SQLite; Manim and its dependencies, PyAV's FFmpeg libraries among them; TinyTeX; Typst; and the WebView2 runtime. TinyTeX contains TeX programs under their own licenses. Typst's Apache-2.0 `LICENSE` and `NOTICE` ship beside it in `runtime\typst`. WebView2 is Microsoft software under Microsoft's terms, not part of Mathimator's source. The Windows shell uses it. `scripts/generate_notices.py` writes `THIRD_PARTY_NOTICES.txt` from the built bundle: each component with its version and license, the license texts that the components or `packaging/licenses` provide, and the written source offer. The installer ships it as `runtime\THIRD_PARTY_NOTICES.txt`, and the bundle check of section 8 fails while it is missing or was written for other lockfiles. The Notices panel shows a short summary and then that file (section 6). Approving MIT approves this split. It is not a legal opinion.

Other Manim front ends exist (manim-slides, manim-editor, manim-studio, JAnim). v1 does not use them as a base or as a library.

### Boundaries

Section 9 is the full out-of-scope list. These boundaries define v1:

- One Windows installer. No Mac or Linux installer.
- Cairo only. No ManimGL, no OpenGL renderer, no interactive 3D camera, no OpenGL window parented into the webview.
- No freeform Python editor, plugins, accounts, cloud render, or AI scene generation.
- No separate FFmpeg program, though PyAV's FFmpeg libraries ship in the render stack, and no Tectonic.
- No scene longer than 120 seconds.
- No intra-beat rerender. A changed beat rerenders that whole `play` or `wait`.

### What the window contains

- Top bar: project name, project folder path, New, Open, Save As…, Add, Undo, Redo, Export, View code, Notices, Sample, Clear cache. New, Open, Save As… and Sample are disabled from the Export click until the export ends (D22).
- Under it: the status line, with Cancel export while an export runs; a TeX warning while formulas cannot render; and a message area that holds the error banner, the "Worker unavailable" panel with its reason and Retry, the shell's notices, each with Dismiss, and the export note.
- Center: the preview, a play control, and the playhead.
- Right: the inspector for the selected object, template, or beat, or in its place View code or the Notices.
- Bottom: the beats in order, with derived start times and durations, and the path of the last export.
- Export opens a panel with the preset, resolution, frame rate and transparency, and Export and Re-export (D56).

Add has five entries in the finished app: Axes and function, Formula transform, 3D axes, Formula, and Text. Phases in section 8 add them in order.

## 2. UX timing budgets

These budgets are the targets to approve. The full-preview and export numbers are not measurements. Phase 1 measures them. No budget below claims that Manim has already been benchmarked.

### Reference machine

Windows 11, 8 CPU cores, 16 GB RAM, SSD, integrated graphics, plugged in, Windows power mode set to High performance. The phase report records the CPU name. A result from a different machine does not count as a budget result.

### Reference scene

`reference-60` is the timing fixture. It is a 2D scene, seed 1, background `#000000`, preview profile 854×480 at 15 fps. Its objects are the Journey A objects in section 4: `axes_1` with labels, `plot_1` of `sin(x)`, and `title_1` with LaTeX `y = \sin(x)`. It has no transform. Preview length is 60 seconds, 900 frames. A 1080p30 export is 1800 frames.

Durations are the source of truth. The start column is derived and must not be stored.

| Index | Id | Action | Targets | Duration (s) | Params | Derived start (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | beat_1 | create | axes_1 | 8 | | 0 |
| 1 | beat_2 | create | plot_1 | 10 | | 8 |
| 2 | beat_3 | write | title_1 | 6 | | 18 |
| 3 | beat_4 | wait | | 4 | | 24 |
| 4 | beat_5 | set_color | plot_1 | 8 | color `#E07A5F` | 28 |
| 5 | beat_6 | move | title_1 | 8 | shift `[0, -0.4]` | 36 |
| 6 | beat_7 | wait | | 8 | | 44 |
| 7 | beat_8 | set_color | title_1 | 8 | color `#F2CC8F` | 52 |

The sum is 60 seconds. Axis labels and the title use LaTeX, so the timing includes TeX. The fixture produces 8 partial movies, one per beat.

### Measurement conditions

- Wall clock on the reference machine. One scene. No second render running.
- "Cold cache" means the Manim partial movies for that scene and profile have been deleted. The operating system's file cache may be warm. TinyTeX has been run at least once on that machine, so font generation is warm, except on the phase 5 clean-machine test.
- UI-only budgets fail if the worker receives a render command during the action.
- A scripted harness timestamps the input and the resulting UI state. Python behavior is covered by automated tests. Latency checks run against a build on the reference machine, and the numbers go in the phase report.

### Budgets

| User action | Deadline | What is measured | How to test |
| --- | --- | --- | --- |
| Select an object or beat, undo, redo, or drag a beat duration | 50 ms | UI state only. Selection highlight, restored document, or the duration label and derived starts. | Script the input. Assert the change is visible within 50 ms and the worker log gained no render command. During a duration drag, `project.json` is not written until pointer-up. |
| Scrub the playhead on a finished preview | 100 ms | The frame under the playhead. Playback and scrub do not run Manim. | On a finished `reference-60` preview, seek to 9.0 s (1 second into beat index 1). The frame matches that partial movie within one preview frame, within 100 ms. The worker log shows no Manim process. |
| Edit one display equation in the supported subset | 100 ms debounce, then 200 ms | Typst glyph preview. This is not a scene render. | Type `y = \sin(x)`. When the 100 ms debounce fires, the SVG is visible within 200 ms. Manim is not started. |
| Commit a color, position, or duration edit on one beat of at most 5 seconds, with earlier beats cached | 3 s | That beat's updated partial movie is playing. | Setup, untimed: render `reference-60`, shorten the edited beat to 5 s, and wait until it is fresh. Timed cases, from that state: change beat 8's color; change beat 8's duration from 5 s to 4 s; change beat 6's shift after setup has shortened beat 6 to 5 s. The new partial plays within 3 s. Partial movies for earlier beats keep the same hashes. |
| Insert Axes and function into an empty scene | 8 s | Both default beats are visible: axes with labels, and `y = sin(x)`. | Worker already warm. Cold preview cache. Click the template. Default durations are 2 s and 2 s. Both partial movies are on screen within 8 s. |
| Insert the 3D axes template into an empty scene | 20 s | Axes and the default surface are visible. | Same conditions. Default surface resolution is 8×8. Both partial movies are on screen within 20 s. This budget is separate because a Cairo surface is not the 2D insert. |
| Full uncached preview of `reference-60` | 45 s | Preview playable from 0 s through 60 s. | Cold preview cache, preview profile. If the time is over 67.5 s, stop and revise this spec before adding features. |
| Click Export | 200 ms to progress; 1 s to cancel; 3 min to finish the budgeted export | Progress UI, cancel, and a finished 1080p30 MP4 of `reference-60`. | Progress text "Rendering export" is visible within 200 ms of the click, without waiting for Manim's first log line. Cancel: the grandchild and any TeX child are gone within 1 s, and no new finished file appears afterward. Cold export cache, 1920×1080, 30 fps, MP4: the file plays and is within one frame of 60 s. If that export takes over 270 s, stop and revise this spec before adding features. |
| Launch to an empty project | 8 s | Window accepts a click, and the worker has passed the shell's handshake. | No last project to reopen, so launch starts a new temp project (section 6). Second process start within a minute of a previous start, so a first-time security scan is not the measurement. No save dialog. The empty project is interactive and the worker is ready within 8 s. |
| Open a saved project | 3 s | Interactive canvas. Object list and timeline accept a click. | The project is already chosen; dialog browsing is not included. The worker is already running. The preview may be stale and marked out of date. No Manim run is required before the canvas is interactive. |

The 3 second clock stops when the edited beat's new partial movie is playing. It does not wait for later beats. The test uses a beat shortened to 5 seconds, and the hashes of earlier partial movies stay the same. Later beats may still be dirty at the deadline; each becomes playable when its own partial movie is ready. An edit still marks that beat and every beat after it dirty, because that is how Manim's partial-movie cache works. The canvas keeps the last good frames and the timeline shows the dirty range. v1 does not rerender inside a beat.

Clocks for the 3 second, 8 second, and 20 second budgets start at the committing input (click, Enter, or pointer-up) and include a 300 ms preview debounce. The 200 ms equation budget starts when its own 100 ms debounce fires. Selection, scrub, undo, and in-drag timeline updates are not debounced into a render.

The 45 second, 3 minute, and 20 second targets use the 1.5× revise rule (67.5 s, 270 s, and 30 s). The other budgets are pass/fail. A miss stops the phase either way.

A new user can build axes, a sine curve, one equation, and an MP4 without opening code. View code and Export code are explicit actions.

## 3. Three end-to-end journeys

These journeys are the finished v1 app. Section 8 says which phase first runs each one.

`lesson-60` is the saved project for Journey B. Objects: `axes_1` and `plot_1` as in Journey A, `eq_1` with LaTeX `y = \sin(x)`, `eq_2` with LaTeX `y = \cos(x)`. Both formulas use anchor `edge_up` and color `#FFFFFF`. Durations sum to 60 seconds.

| Index | Action | Targets | Duration (s) | Notes |
| --- | --- | --- | --- | --- |
| 0 | create | axes_1 | 8 | Does not mention a formula. |
| 1 | create | plot_1 | 8 | Does not mention a formula. |
| 2 | write | eq_1 | 6 | Entrance of `eq_1`. |
| 3 | wait | | 8 | |
| 4 | set_color | plot_1 to `#E07A5F` | 8 | |
| 5 | wait | | 8 | |
| 6 | transform_formula | eq_1, eq_2 | 8 | Entrance of `eq_2`. |
| 7 | wait | | 6 | The tail after the transform. |

### Journey A — First animation

Steps:

1. Launch the app and choose New. Name the project "Sine introduction".
2. Add → Axes and function. Leave the expression as `sin(x)`.
3. Add → Formula. Enter `y = \sin(x)`. Leave the anchor at the top edge.
4. Scrub the playhead across the timeline.
5. Export an MP4 at 1920×1080, 30 fps.

The user sees axes and a sine curve after step 2, then a title formula. The equation editor shows a Typst glyph and the caption "Live preview is Typst. The video uses LaTeX and will look different." Scrub moves the picture. Export shows "Rendering export", then a file path. No code view opens during these steps.

Underneath, the app writes `project.json`, generates `MainScene`, and renders a preview with the Cairo CLI in section 5. Typst compiles one SVG for the glyph. The scene file uses `MathTex`. Scrub reads the preview file. Export starts a 1080p30 render in the shared media directory and copies the finished MP4 into the export folder. The preview file is not that MP4.

Failure: the user has to open code to place the curve or to get the file; the axes and curve are not both visible within 8 seconds; scrub invokes Manim or misses the frame; progress appears late; or the exported file is 854×480 or otherwise taken from the preview cache. Journey A is shorter than `reference-60`, so the 3 minute budget is judged on `reference-60`, not on this scene.

### Journey B — Revise a lesson

Steps:

1. Open a saved `lesson-60`. The canvas is interactive within 3 seconds. The preview may say "Preview is out of date".
2. Change `eq_1` to `y = \sin(2x)`.
3. Change `eq_2` to `y = \cos(2x)`.
4. Scrub into the tail (the last wait).
5. Undo once.
6. Export a GIF at the GIF defaults.

The user sees the dirty marks. After step 2, the write of `eq_1` and every beat after it are dirty, and the axes and plot creates stay cached when their partial movies match. Step 3 edits `eq_2` inside that tail. The dirty range does not grow to include the axes. Scrub of a dirty beat keeps the last good frame and the badge "Preview is out of date". Undo restores `eq_2` to `y = \cos(x)`. The GIF preset shows a disabled transparency control with the text "GIF cannot store an alpha channel."

Underneath, codegen keeps the source for beats before the first dirty beat byte-identical, then runs Manim on the whole scene so the partial-movie cache can reuse the prefix. Export GIF is an export-quality render with `--format gif` and without `--transparent`.

Failure: changing only `eq_2` rerenders the axes create; scrub calls `construct` or drops the last good frame; undo leaves `y = \cos(2x)` in place; the GIF control offers transparency; or the GIF is a copy of the preview MP4.

### Journey C — Reuse a template

Steps:

1. Choose New for an empty project.
2. Add → 3D axes.
3. Leave the default surface `sin(x)*cos(y)`, or switch the control to the parametric curve. Set phi, theta, and zoom.
4. Play the preview in the canvas.
5. Export a transparent WebM, or a transparent PNG sequence.

The user sees number fields for a fixed camera. There is no orbit drag and no second window. The surface uses resolution 8×8 unless the user changes it. The preview is the Cairo movie inside the Mathimator window. For WebM and PNG sequence, the transparency control is enabled. The surface fill stays opaque; transparency is the background.

Underneath, the app sets the scene kind to `three_d_scene`, generates the Cairo `ThreeDScene` in section 5, and passes `--renderer cairo`. Camera orientation is a single `set_camera_orientation` call before the beats. The app does not call `begin_ambient_camera_rotation` and does not pass `--live-preview` or `--renderer opengl`. Transparent WebM uses `--format webm --transparent`. Transparent PNG sequence uses `--format png-sequence --transparent`. `--format png` is the wrong flag: in Manim 0.22 it writes the last frame only.

Cairo limits, stated in the inspector for this template: software rendering, flat-shaded faces, a static camera, and a resolution cap of 24×24. The 8 second budget does not apply. The 20 second budget does. A 3D template is refused unless the project has no objects and no beats ("A 3D template needs an empty project."). A 2D template is refused in a 3D project ("This template is for a 2D scene."). A formula or text object is refused there too ("A 3D scene only holds the 3D template."). A 3D project contains the template's axes and one surface or curve.

Failure: any window other than Mathimator opens; the command line contains `opengl` or `--live-preview`; the export is 854×480; transparency is offered for MP4; or the axes and surface are not both visible within 20 seconds. A 20 second miss over 30 seconds revises this spec before the next phase.

## 4. Scene data model

A project is a folder. `project.json` is the document. Everything under `.mathimator/` is derived, except the lock.

```text
<project>/
  project.json
  .mathimator/
    lock
    temp-project.json          the shell's marker, in temp projects only
    codegen/scene.py
    cache/media/
    cache/last-good/
    cache/preview-index.json
    cache/rendered.json
    cache/manim-run.json       only while Manim runs
    exports/<key16>/           the export and its export.json
    equation/                  formula-<slot>-<generation>.typ and .svg
    logs/worker.log
    logs/worker.log.1
```

`preview-index.json` is the preview index of section 6, its paths relative to the project folder. `rendered.json` records the scene stamp and the beat keys of the last full preview, which the dirty flags are derived from. `manim-run.json` records the Manim run in progress (section 6). The `export.json` beside an export records its full `exportKey`.

Deleting `.mathimator/cache`, `codegen`, `equation`, or `logs` is safe. Clear cache deletes them, and the `typst/` folder older builds used for glyph previews, except the active `worker.log`, which it empties. It leaves `project.json`, `.mathimator/exports/`, `lock`, and the shell's temp-project marker `temp-project.json` in place, and it is refused while a render runs or waits. A read-only file or folder there is made writable and deleted. A file another program holds open stays, and the reply says "Some cache files are in use and were not removed. Close other programs using this project and try again." v1 does not call `manim cache clear`, and it does not pass `--flush_cache` (removed in 0.22). The cache v1 owns is the project cache directory.

`worker.log` records the commands, Manim's working folder, command line, process id and output, and every error with its traceback. `state` and `code` replies are recorded by size only. At 5 MB the log becomes `worker.log.1`, which replaces the older copy.

One window works on a project at a time. While a project is open, its worker holds an operating-system lock on `.mathimator/lock`, and a second window that opens the folder is told "This project is open in another Mathimator window." The lock ends with the worker, so a crash leaves none behind. On Windows the lock file stays open, so while a project is open Windows cannot rename, move or delete its folder, as with a document open in Word. This amends D57, which allowed moving an open project. The checks of section 6 still notice a folder that went away while it was open: an unplugged drive, a dropped network share, or a folder renamed or moved on Linux or macOS, where the lock does not hold the folder.

Mathimator keeps its files inside the project, so none of its own folders, nor its log, may be a symbolic link or a junction: `.mathimator`, its `cache`, `cache/media` with its `Tex`, `videos/scene` and `images/scene/MainScene` folders, `cache/last-good`, `codegen`, `exports`, `equation`, `logs` and `logs/worker.log`, and every folder between one of them and the project folder. A project with such a link is refused with `linked_folder`: "Mathimator cannot use <path>: it is a link to another location, and Mathimator keeps its files inside the project. Delete the link and try again." The worker checks when it binds a folder, when a job starts, when it publishes an export, and in Clear cache (section 6). No delete in a project follows a link: a link is removed itself, never what it points to, and nothing below a link is deleted. The worker never writes a log line through a link; that line goes to stderr.

The worker remembers the bytes of `project.json` as it last read or wrote them. When an edit, undo, redo or render starts and the file on disk differs, nothing is written and the reply says "project.json was changed outside Mathimator."; Reload reads the file and clears undo history, and Overwrite writes the open document over it. When the project folder is gone, for example on an unplugged drive, the change is refused with "The project folder was moved or deleted." The worker knows the folder by its identity, `(st_dev, st_ino)`, which on Windows is the volume serial number and the file id, so a different folder at the same path counts as gone too. The edits saved before stay in the app, and Save As writes them to another folder. The worker never creates a project folder and never writes an empty `project.json` over a missing one.

On save, `project.json` is pretty-printed with two-space indentation, a trailing newline, uppercase `#RRGGBB` colors, and stable key order. The same document saved twice has the same bytes. Hashing uses a separate canonical form: UTF-8, sorted keys, no insignificant whitespace, array order preserved, numbers with three decimal places. A save writes a new file beside `project.json`, flushes it to disk, and renames it over the old one, retrying for about half a second while Windows reports the file in use. A save that still fails is undone, and the reply says "Could not save project.json: <reason>. The change was undone." An edit whose saved bytes would not change, such as the current color typed in lower case, writes nothing, adds no undo step and keeps Redo.

`project.json` is UTF-8 text. One leading byte order mark is ignored, and saves never write one. A file in any other encoding, such as UTF-16 with or without a byte order mark or an ANSI code page, is refused with "project.json must be saved as UTF-8 text." Every refusal of a document names the JSON path of the failing value, for example `scene.objects[2].params.latex`.

### Project

| Field | Rule |
| --- | --- |
| `schemaVersion` | Integer. v1 reads and writes `1` only. Any other value, or a missing value, is refused; `true` and `1.0` are not integers. The message names the file's version. |
| `generatorVersion` | Integer `2` for code that matches section 5. Bump it when generated Python changes. It is part of the cache key. An older version opens as the current one: its beats are dirty, its exports are out of date, and the next save writes the current number. A newer version, `0`, or a value that is not an integer is refused. |
| `id` | Stable id matching `[a-z][a-z0-9_]{0,31}`. |
| `name` | Display name. Not used in codegen. Renaming it does not dirty a preview. |
| `seed` | Integer from 0 to 4294967295, the range numpy accepts. v1 always writes `1` and shows no seed control. |
| `scene` | The only scene. |
| `previewProfile` | Fixed: width 854, height 480, fps 15, `transparent` false. |
| `exportProfile` | `format` (`mp4`, `gif`, `webm`, or `png_sequence`), `width`, `height`, `fps`, `transparent`. |

There is no scene list. There is no `start` field anywhere. A file that contains `start`, an unknown key, or an unknown object type is refused.

Strings may hold tab, line feed and carriage return, so CRLF text from Windows tools opens. A string with NUL, another C0 control character or a lone UTF-16 surrogate is refused on open and in an edit, naming the field. A number too large for a float, `NaN` and `Infinity` make the file invalid JSON. Codegen writes numbers with three decimal places, so a document number is checked at that precision: a step, length or zoom that rounds to 0, or a range or domain whose maximum rounds to its minimum, is refused. The file keeps the number as written. A number inside an expression is not a document number: codegen uses it exactly as typed (section 5).

### Scene, objects, beats

| Field | Rule |
| --- | --- |
| `scene.id`, `scene.name` | Id rule as above. Name is display only. |
| `scene.kind` | `scene` or `three_d_scene`. |
| `scene.background` | `#RRGGBB`. A change dirties every beat. |
| `scene.camera` | Present only for `three_d_scene`: `phi` and `theta` in degrees, `zoom` as a positive number. A change dirties every beat. |
| `scene.objects` | Array of objects. |
| `scene.beats` | Timeline order. Position in this array is the beat index and the Manim animation index. |
| `scene.templateInstances` | Array of `{id, templateId, params, objectIds, beatIds}`. |

Object fields: `id`, `name`, `type`, `params`. Beat fields: `id`, `duration`, `action`, `targets`, and `params` when the action needs them. Ids are unique across the scene, objects, beats, and template instances. An object id is a variable in the generated code, so it is not a Python keyword, `np`, `self`, or `config`, and it does not collide with a name codegen makes: `<axes>_labels` and `<axes>_group` for any 2D axes, whatever `showLabels` says, `u` and `v` for the axes of a surface, and `t` for the axes of a curve. A file with such an id is refused, naming the id. The playhead, selection, dirty flags, and undo stack are not stored. Undo history holds at least 50 document edits per session and is discarded on exit.

Derived start of beat `i` is the sum of durations of beats `0 .. i-1`. The timeline displays it and does not accept an edit to it. Total duration is the sum of beat durations. Minimum beat duration is 0.1 seconds. Maximum total is 120 seconds. A committing edit that breaks either limit is refused, and the document is unchanged.

### Object types

| Type | Used in | Params |
| --- | --- | --- |
| `axes` | 2D | `xRange`, `yRange` as `[min, max, step]`, `xLength`, `yLength`, `color`, `showLabels` |
| `function_plot` | 2D | `axesId`, `expression`, `xMin`, `xMax`, `color` |
| `math_formula` | 2D | `latex` (no delimiters, at most 500 characters, allowlisted commands only), `color`, `anchor`, `shift` |
| `text` | 2D | `text` (at most 200 characters), `color`, `anchor`, `shift` |
| `three_d_axes` | 3D | `xRange`, `yRange`, `zRange` as `[min, max, step]`, lengths optional and defaulted by the template |
| `surface` | 3D | `axesId`, `expression` in `x` and `y`, `uRange`, `vRange`, `resolution` as `[u, v]`, `color` |
| `parametric_curve` | 3D | `axesId`, `components` as three expressions in `t`, `tRange` as `[min, max]`, `color` |

`anchor` is `origin`, `edge_up`, `edge_down`, `edge_left`, or `edge_right`. `shift` is `[dx, dy]` in Manim units, y up. Omitted `shift` means `[0, 0]`.

A project may come from someone else, and TeX reads files when a formula asks it to. Formula LaTeX is therefore limited to an allowlist of math commands from LaTeX, `amsmath` and `amssymb`, the packages of Manim's default template: Greek and Hebrew letters, math symbols, operators, relations, arrows, big operators, function names such as `\sin` and `\lim`, delimiters with `\left`, `\right` and the `\big` family, dots, fractions, roots, binomials, accents, braces over and under, math fonts such as `\mathbb`, `\text` and `\operatorname`, styles, and spacing. Control symbols are limited to spacing (`\,` `\:` `\;` `\!`), `\\`, the escaped specials `\{` `\}` `\|` `\%` `\&` `\#` `\$` `\_`, and a backslash before whitespace or at the end, which TeX reads as a space. `\begin` and `\end` take only `matrix`, `pmatrix`, `bmatrix`, `Bmatrix`, `vmatrix`, `Vmatrix`, `smallmatrix`, `cases`, `aligned`, `gathered`, and `array`, because `\begin{name}` runs `\name`. `\hline` draws the rules of an `array`; `\cline` is not on the list and is refused (D04). `^^` is refused anywhere, because it spells a character by its code. Anything else, including `\input`, `\csname`, `\catcode`, `\def`, `\write`, and `\immediate`, is refused on open and in an edit with `untrusted_latex` and "<field> uses \<command>, which formulas cannot use." The same check applies to the LaTeX in `formula_transform` template params.

An axis draws at most 500 ticks: `(max - min) / step` must be below 500, for 2D and 3D axes and for the ranges in template params. Manim samples a function plot ten times per x step of its axes, so `xMax - xMin` must also be below 500 x steps. Manim samples a parametric curve every 0.01 of t, so its `tRange` must span less than 50, which is 5000 samples like a plot's. These two are the plot-sampling bounds of D04. These refusals name the limit, as the 24×24 resolution cap does.

v1 has no arrow and no dot. The journeys do not need them, and there is no general mobject factory.

Expressions use a fixed grammar. `expr` is terms joined by `+` or `-`. A term is factors joined by `*` or `/`. A factor is a unary minus followed by a factor, or a power. A power is a primary, optionally followed by `^` and an exponent, which is one primary after any number of unary minuses. A primary is a number, the variable, `pi`, a call of `sin`, `cos`, `tan`, `exp`, `log`, or `sqrt` with one argument, or parentheses. A number is digits with at most one decimal point, such as `2`, `0.5`, `.5`, or `1.`, and has no exponent part. The 2D variable is `x`. A surface uses `x` and `y`. A curve uses `t`. `log` is the natural log, and the inspector says so. As in usual math and in Python, `^` binds tighter than a unary minus, which binds tighter than `*` and `/`, which bind tighter than `+` and `-`. So `-x^2` means `-(x^2)`, `exp(-x^2)` is a bell curve, and `2^-x` means `2^(-x)`. `x^2^2` is refused; write `(x^2)^2` or `x^(2^2)`.

Rejected examples, with no render started: `np.sin(x)`, `sin(x, 1)`, `y` in a 2D plot, `__import__("os")`, `x^2^2`, `1e3`. The field shows "This expression is not supported." The same text refuses an expression longer than 1000 characters, one nested deeper than 64 levels (each parenthesis, call and unary minus is a level), and a number literal too large for a float.

### Actions

| Action | Targets | Role |
| --- | --- | --- |
| `create` | one | Entrance for axes, function plots, 3D axes, surfaces, curves. |
| `fade_in` | one | Entrance allowed for every object type. |
| `write` | one | Entrance for `math_formula` and `text` only. |
| `wait` | none | Hold. |
| `transform_formula` | source, destination | One `play`. Destination must be a `math_formula` whose entrance is this beat. Source is a `math_formula` on screen; the transform takes it off screen. |
| `set_color` | one on screen | `params.color`. Axes with labels change colour; their labels keep theirs. |
| `move` | one on screen | `params.shift` as `[dx, dy]`. Axes with labels move with their labels. Plots and other objects drawn on the axes stay where they are; their own beats move them. |

Every object has exactly one entrance beat. A beat may mention an object only at its entrance or after it. A function plot, surface or curve mentions its axes, so it enters after them. `transform_formula` is the destination's entrance, so a transformed formula has no separate `create`. A transform source is off screen from then on: a later `set_color`, `move`, or second `transform_formula` on it would bring the old formula back in the video, so the file is refused, naming the beat, for example "scene.beats[5] changes eq_1, but eq_1 left the screen when it was transformed into eq_2". Deleting the destination also deletes the transform beat, which puts the source back on screen for the beats after it.

### Templates

A template instance is a recorded insertion, not a plugin. Its fields are `id`, `templateId`, `params`, `objectIds`, and `beatIds`. Params are the source of truth for the objects it created. Editing those params rewrites those objects in place and keeps ids, beat ids, beat actions, and beat durations. On open, linked objects are rewritten from the instance so a hand edit to a generated object param does not survive. An edit that would change such a param without changing the instance is refused with `invalid_edit` and "<field> is set by its template, so this edit cannot change it.", where `<field>` is the param's JSON path. User beats that sit between template beats are left in place. Switching the 3D template between surface and curve replaces the second object, retargets the second beat, and keeps both beat ids. There is no template directory and no third-party loader.

| Template id | Inserts | Defaults |
| --- | --- | --- |
| `axes_function` | `axes`, `function_plot`, two `create` beats | `sin(x)`, ranges as in the Journey A JSON, labels on, color `#58C4DD`, durations 2 s and 2 s |
| `formula_transform` | two formulas, a `write` beat, a `transform_formula` beat | `y = \sin(x)` to `y = \cos(x)`, anchor `edge_up`, durations 2 s and 2 s |
| `three_d_axes` | `three_d_axes`, one `surface` or `parametric_curve`, two `create` beats, camera | Axes ranges `[-3, 3, 1]`, `[-3, 3, 1]`, `[-2, 2, 1]`. Surface `sin(x)*cos(y)`, `u`/`v` range `[-2.5, 2.5]`, resolution `[8, 8]` with a maximum of `[24, 24]`, phi 65, theta −45, zoom 1, durations 2 s and 2 s. Curve mode uses components `cos(t)`, `sin(t)`, `t / 3` and `tRange` `[0, 12.56]`. |

New beats and template insertions are placed after the selected beat, or appended when nothing is selected. Deleting an object also deletes every beat that targets it, as one undo step, and dirties from the first removed index.

### Cache identity

A beat's cache key is the SHA-256 of Manim version `0.22.0`, `generatorVersion`, the active profile (preview or export), the beat index, the beat's canonical JSON, the canonical JSON of the objects that beat mentions (a function plot includes its axes; a surface or curve includes its axes), and the seed.

`sceneHash` is the hash of `schemaVersion`, `generatorVersion`, `seed`, and `scene`. `exportKey` is `sceneHash` plus the export profile plus the Manim version. The finished export folder is named with the first 16 hex characters of `exportKey`. A manifest beside the file stores the full hash, and a mismatch is not the current export. The Manim media directory is not named with these hashes. Putting the scene hash in the media path would discard partial-movie reuse. Encoder settings stay at Manim's defaults; v1 does not pass `--video-codec` or `--encoder-option`. Changing those defaults in a future Manim pin requires a new `generatorVersion`.

Display names are left out of every beat key, `sceneHash` and `exportKey`, so a rename neither dirties a preview nor discards an export.

Dirty flags are derived from these keys. They are not stored in `project.json`. Beats are dirty from the first one whose key differs from the last full preview, and all of them when the scene stamp differs: the hash of the background, the camera, the scene kind, `schemaVersion`, `generatorVersion` and the seed. A preview file counts as fresh only under its beat's chain key, the scene stamp chained with the keys of that beat and of every beat before it, because a partial movie shows the scene as the earlier beats left it (section 6).

### Journey A document

This is the normative `project.json` for Journey A.

```json
{
  "schemaVersion": 1,
  "generatorVersion": 2,
  "id": "sine_intro",
  "name": "Sine introduction",
  "seed": 1,
  "scene": {
    "id": "scene_main",
    "name": "Main",
    "kind": "scene",
    "background": "#000000",
    "objects": [
      {
        "id": "axes_1",
        "name": "Axes",
        "type": "axes",
        "params": {
          "xRange": [-6.5, 6.5, 1],
          "yRange": [-1.5, 1.5, 0.5],
          "xLength": 10,
          "yLength": 4,
          "color": "#FFFFFF",
          "showLabels": true
        }
      },
      {
        "id": "plot_1",
        "name": "Sine",
        "type": "function_plot",
        "params": {
          "axesId": "axes_1",
          "expression": "sin(x)",
          "xMin": -6.28,
          "xMax": 6.28,
          "color": "#58C4DD"
        }
      },
      {
        "id": "title_1",
        "name": "Title",
        "type": "math_formula",
        "params": {
          "latex": "y = \\sin(x)",
          "color": "#FFFFFF",
          "anchor": "edge_up",
          "shift": [0, 0]
        }
      }
    ],
    "beats": [
      {
        "id": "beat_1",
        "duration": 2,
        "action": "create",
        "targets": ["axes_1"]
      },
      {
        "id": "beat_2",
        "duration": 3,
        "action": "create",
        "targets": ["plot_1"]
      },
      {
        "id": "beat_3",
        "duration": 2,
        "action": "write",
        "targets": ["title_1"]
      },
      {
        "id": "beat_4",
        "duration": 1,
        "action": "wait",
        "targets": []
      }
    ],
    "templateInstances": [
      {
        "id": "tmpl_1",
        "templateId": "axes_function",
        "params": {
          "expression": "sin(x)",
          "xRange": [-6.5, 6.5, 1],
          "yRange": [-1.5, 1.5, 0.5],
          "color": "#58C4DD"
        },
        "objectIds": ["axes_1", "plot_1"],
        "beatIds": ["beat_1", "beat_2"]
      }
    ]
  },
  "previewProfile": {
    "width": 854,
    "height": 480,
    "fps": 15,
    "transparent": false
  },
  "exportProfile": {
    "format": "mp4",
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "transparent": false
  }
}
```

## 5. Mapping timeline to Manim code

The generated file defines `MainScene` and does not instantiate it. Quality, format, and transparency are command-line arguments, so the same file is the preview and the export. The worker runs it with the bundled interpreter, in the folder that holds it, `<project>/.mathimator/codegen`:

```text
<python> -P --check-hash-based-pycs always -m mathimator_worker.manim_main render scene.py MainScene --config_file <worker>/mathimator_worker/manim.cfg --silent --renderer cairo --seed 1 -r 854,480 --fps 15 --format mp4 --media_dir ../cache/media --max-inflight-encoders 1 --progress_bar none
```

`mathimator_worker.manim_main` runs Manim's own command line, `python -m manim`, with two fixes to Manim 0.22's scene writer. Manim 0.22 writes the segment list that FFmpeg joins with each path in single quotes and does not escape a quote inside one, so an apostrophe in the project path failed every video. The list writes it as `'\''`, which is how FFmpeg reads one. And Manim hands a static wait to the scene writer as one frame with a repeat count, which an image sequence saved once, so a 1 s wait at 30 fps was one PNG. The writer now saves an image sequence's frame once per repeat. Movies are written as before.

Manim runs isolated from the PC's own settings:

- The folder it runs in is also its home folder (`HOME` and `USERPROFILE`). The worker writes that folder afresh before each render, holding only `scene.py`, so no per-user or per-folder `manim.cfg` applies.
- The worker ships `manim.cfg` with `max_files_cached = 2400`. Manim deletes its least recently used cached clips above that count, and a document has at most 120 s / 0.1 s = 1200 beats, so the clips of the render that just finished are never deleted.
- `--silent` stops the check for a newer Manim on PyPI that Manim otherwise makes after every render. Manim never uses the network.
- `-P` keeps the folder off `sys.path`, and `--check-hash-based-pycs always` makes Python check bytecode against its source, so nothing planted beside the scene runs in its place.
- LaTeX runs with `shell_escape=f`, `openin_any=p`, and `openout_any=p` in its environment, which kpathsea reads before `texmf.cnf`. Every inherited variable whose name starts with one of those is dropped, such as `shell_escape_latex`, which kpathsea would read first, so LaTeX runs no shell command whatever the user set.
- Before each render the worker empties Manim's TeX folder, `.mathimator/cache/media/Tex`, of everything but the SVG files Manim reuses, and removes a link there without following it. TeX reads its inputs from that folder before its own, and Manim compiles any TeX source or DVI file it finds there, so a file planted in a shared project could otherwise read local files past the formula allowlist of section 4. A file that cannot be removed fails the render with `render_failed`.

The paths are relative to that folder, so TeX never reads the user's folder names, and a project path with `%`, `~`, `^^`, or `'` renders. Before Manim starts, a job checks the project path, and a path that still cannot render is refused with `path_unsupported`. The message names the folder and the reason, and asks for a move to a shorter or plainer path:

- `{` in the path: Manim formats its absolute scene path as a template while it holds one, whatever folder it runs in. D61 counted `{` among the characters that running from the project folder fixes; it is refused instead.
- On Windows, characters outside the ANSI code page: dvisvgm reads its arguments and its folder in that code page. Manim then runs through the 8.3 alias of the project folder when Windows keeps one. Without an alias only a scene with no formula and no axis labels renders. Every path the worker reports stays in its long form.
- On Windows, length: dvisvgm cannot open a path of 260 characters or more, even with Windows long paths on, so a scene with a formula or axis labels needs a project path of at most 210 characters. Without long paths Python cannot either, so every render needs one of at most 114 characters, the room the longest partial movie name leaves.

A scene with a formula or axis labels is also refused, with `tex_unavailable`, while the shell reports TeX unavailable: the bundled TeX is missing or failed its self-test.

Preview and export use that same `--media_dir`. The export command changes `-r`, `--fps`, and `--format`, and adds `--transparent` only when that format allows it. Manim separates quality variants in folders under the media directory, so a 854×480 partial is not a 1920×1080 partial. `--max-inflight-encoders 1` keeps Manim's default: each partial movie is finished before the next beat starts. The worker never passes `--preview`, `--live-preview`, `--renderer opengl`, `--disable_caching`, `--flush_cache`, or `--from_animation_number`.

`-n` / `--from_animation_number` is an animation index, not a timestamp. v1 always renders the whole scene and lets Manim reuse earlier partial movies. Seeking does not use that flag.

`Scene.update_to_time` only advances the animation already inside `play_internal`. Cairo `Camera.get_image()` returns the frame the camera is holding. Neither call seeks to an arbitrary time. The scrub path does not call them, and it does not call `construct`.

### Journey A, normative output

```python
# Generated by Mathimator. generatorVersion 2. manim==0.22.0
from manim import *
import numpy as np

config.seed = 1


class MainScene(Scene):
    def construct(self):
        self.camera.background_color = "#000000"

        axes_1 = Axes(
            x_range=[-6.5, 6.5, 1],
            y_range=[-1.5, 1.5, 0.5],
            x_length=10,
            y_length=4,
            axis_config={"color": "#FFFFFF"},
        )
        axes_1_labels = axes_1.get_axis_labels(x_label="x", y_label="y")
        axes_1_group = VGroup(axes_1, axes_1_labels)
        self.play(Create(axes_1_group), run_time=2)

        plot_1 = axes_1.plot(
            lambda x: np.sin(x),
            x_range=[-6.28, 6.28],
            color="#58C4DD",
        )
        self.play(Create(plot_1), run_time=3)

        title_1 = MathTex(r"y = \sin(x)", color="#FFFFFF")
        title_1.to_edge(UP)
        self.play(Write(title_1), run_time=2)

        self.wait(1)
```

`config.seed` is set at import. The worker also passes `--seed 1`. Those two values are the project's seed. Codegen emits no `random` calls and no updaters, because an unseeded random value or an updater busts Manim's hashes.

Each object is constructed immediately before its entrance `play`. Labels belong to the axes entrance, so they are inside that one `play`, not a second beat. `duration` in the table is the beat's length under the frame rule below: the duration itself for a beat between whole seconds. Document numbers that are whole are emitted as integers; other document numbers use up to three decimal places with trailing zeros removed. Numbers in an expression are used exactly as typed: whole numbers print as integers and others as Python's shortest exact form, so `0.0004*x^3` keeps its `0.0004`.

| Action | Emitted call |
| --- | --- |
| `create` | `self.play(Create(target), run_time=duration)` |
| `fade_in` | `self.play(FadeIn(target), run_time=duration)` |
| `write` | `self.play(Write(target), run_time=duration)` |
| `wait` | `self.wait(duration)` |
| `transform_formula` | `self.play(TransformMatchingTex(source, dest), run_time=duration)` |
| `set_color` | `self.play(target.animate.set_color("#RRGGBB"), run_time=duration)`. For axes with labels, `target` is still the axes alone, so the labels keep their colour. |
| `move` | `self.play(target.animate.shift(np.array([dx, dy, 0.0])), run_time=duration)`. For axes with labels, `target` is `<id>_group`, the `VGroup` of the axes and their labels, so the labels move with them. |

Anchors: `origin` is `move_to(ORIGIN)`; `edge_up`, `edge_down`, `edge_left`, and `edge_right` are `to_edge` of `UP`, `DOWN`, `LEFT`, and `RIGHT`. A non-zero shift is a `shift(np.array([dx, dy, 0.0]))` after the anchor.

Expression examples the generator must emit:

| Expression | Lambda body |
| --- | --- |
| `sin(x)` | `np.sin(x)` |
| `sin(x)^2` | `np.sin(x) ** 2` |
| `2*x + 1` | `2 * x + 1` |
| `-x^2` | `-(x ** 2)` |
| `exp(-x^2)` | `np.exp(-(x ** 2))` |
| `2^-x` | `2 ** (-x)` |
| `0.0004*x^3` | `0.0004 * x ** 3` |

`MathTex` receives the stored LaTeX as a raw string. The translator in section 6 is not used here. Generated scene source contains `MathTex` for every formula and does not contain `TypstMath`, `MathTypst`, or `manim.typst`.

`reference-60` uses this same object code. Its `play` / `wait` lines use the section 2 table, including:

```python
self.play(plot_1.animate.set_color("#E07A5F"), run_time=8)
self.play(title_1.animate.shift(np.array([0.0, -0.4, 0.0])), run_time=8)
self.wait(8)
self.play(title_1.animate.set_color("#F2CC8F"), run_time=8)
```

### Beat timing and frames

A beat runs from its derived start to its end, which is the next beat's derived start or the total. At the frame rate of the render, beat `i` covers the frames from `round(start × fps)` up to `round(end × fps)`, with halves rounded up. Every beat boundary in the preview and in every export is therefore within half a frame of the timeline at 15, 30, and 60 fps, so a time in the movie is the same time on the timeline to within half a frame, and a scene of `T` seconds has `round(T × fps)` frames.

A whole second is a whole number of frames at every frame rate. A beat whose start and end are both whole seconds is emitted with its duration as a literal, as in Journey A and `reference-60`. Any other beat calls `_beat_time(start, end)` for `run_time`, or `self.wait(_beat_time(start, end, wait=True))` for a wait, with start and end at three decimals. The generated file then defines the helper between `config.seed` and `MainScene`. It reads `config.frame_rate` when the scene renders, so one `scene.py` still serves the preview and every export:

```python
def _beat_time(start, end, wait=False):
    # Seconds that make Manim draw the frames from start to end, each rounded
    # to the nearest frame, halves up. Times are whole milliseconds, so this is
    # integer math: in floats, 4.1 * 15 is 61.4999... and would round down.
    # A play rounds up and a wait down: hence the half frame.
    rate = config.frame_rate
    frames = (round(end * 1000) * rate + 500) // 1000 - (round(start * 1000) * rate + 500) // 1000
    if wait:
        return (frames + 0.5) / rate
    return max(frames - 0.5, 1) / rate
```

Manim 0.22 draws `ceil(run_time × fps)` frames for a `play` and `floor(seconds × fps)` for a static `wait`, so a `run_time` equal to the duration drifts: a 0.5 s beat is 8 frames of `play` but 7 frames of `wait` at 15 fps. Half a frame of slack keeps either count exact despite floating point. The frame count itself is integer arithmetic on whole milliseconds, as in the UI's scrub, so a boundary on a half frame rounds up on both sides: 4.1 s at 15 fps is frame 62, though 4.1 × 15 is 61.4999… in binary floating point. `tests/fixtures/timing/frame-boundaries.json` lists such boundaries, and the worker's and the UI's tests both check against it. A `play` of one frame takes exactly `1 / fps`, the shortest `run_time` Manim accepts without a warning. With durations of 0.5, 2, 1.25, and 1 seconds, Journey A emits `run_time=_beat_time(0, 0.5)` for the axes, `run_time=_beat_time(0.5, 2.5)` for the plot, `run_time=_beat_time(2.5, 3.75)` for the title, and `self.wait(_beat_time(3.75, 4.75, wait=True))`.

### Invalidation

Editing beat `i` marks beats `i..n` dirty and leaves `0..i-1` cached. The code of each beat before `i` stays byte-identical, so Manim's per-`play` hashes still match. The module header may gain or lose the `_beat_time` helper when a duration edit makes a boundary fractional or whole again, and Manim does not hash it. A duration edit also moves every later beat boundary, which can change a later `_beat_time` call; those beats are in the dirty suffix already.

On `lesson-60`, changing the LaTeX of `eq_2` changes the `MathTex` line for `eq_2` and the `TransformMatchingTex` line. The `Write(eq_1)` line and everything before it stay byte-identical. Dirty beats are index 6 and 7. Changing `eq_1` dirties index 2 through 7, including the transform, because the transform mentions `eq_1`. Changing the axes range changes the `Axes(...)` call, which the first beat already captured, so every beat is dirty.

That last case is the known limitation. A change to an object's definition invalidates from the first beat that mentions the object, which may be the start of the scene. v1 does not rerender part of a beat, and it does not claim a finer cache than Manim's `play` boundary.

### 3D template, normative shape

```python
class MainScene(ThreeDScene):
    def construct(self):
        self.camera.background_color = "#000000"
        self.set_camera_orientation(phi=65 * DEGREES, theta=-45 * DEGREES, zoom=1)

        axes_1 = ThreeDAxes(
            x_range=[-3, 3, 1],
            y_range=[-3, 3, 1],
            z_range=[-2, 2, 1],
        )
        self.play(Create(axes_1), run_time=2)

        surface_1 = Surface(
            lambda u, v: axes_1.c2p(u, v, np.sin(u) * np.cos(v)),
            u_range=[-2.5, 2.5],
            v_range=[-2.5, 2.5],
            resolution=(8, 8),
            fill_color="#58C4DD",
            checkerboard_colors=False,
        )
        self.play(Create(surface_1), run_time=2)
```

Curve mode replaces `surface_1` with:

```python
curve_1 = ParametricFunction(
    lambda t: axes_1.c2p(np.cos(t), np.sin(t), t / 3),
    t_range=[0, 12.56],
    color="#F2CC8F",
)
self.play(Create(curve_1), run_time=2)
```

The header, seed, and imports match Journey A. Degrees in the document are converted with `DEGREES`. The camera call is not a beat. There is no `begin_ambient_camera_rotation`.

## 6. Preview and re-render strategy

### Processes

There are three processes and two hops. The **shell** is the Tauri program: it hosts the UI, starts the supervisor, and runs the glyph preview's translator and Typst. The **supervisor** is a long-lived Python sidecar: it owns the document and the render queue, and it starts Manim. **Manim** renders, one process at a time.

- UI to shell: Tauri commands. Shell to UI: one Tauri event, `worker-event`.
- Shell to supervisor: UTF-8 JSON, one object per line, on the supervisor's stdin. Supervisor to shell: the same on its stdout. The supervisor's stderr is diagnostics only; the shell appends it to `supervisor.err`.

| State | Owner | Others hold |
| --- | --- | --- |
| Document, undo and redo, dirty flags, preview index, render records | Supervisor | The UI keeps a copy from the last `state` reply it applied |
| Project binding, project lock, disk fingerprint, folder identity | Supervisor | The shell mirrors `projectPath` from `state` lines for the files the webview may read, the last project and the glyph folder |
| Render queue: the current job, a held export, a held preview | Supervisor | The shell hands out job ids |
| Which job ids the UI waits for, the stale-preview flag, the status line, the Export button, notes, automatic previews, the `project_changed` prompt | UI | |
| The supervisor process: spawn, handshake, requests in flight, request order, timeouts | Shell | |
| Temp projects, the last project, pruning, the leave warnings, one dialog at a time | Shell | The supervisor leaves the temp-project marker alone |
| Runtime paths, TeX status, log folder, notices | Shell | The UI shows notices and dismisses them. The supervisor keeps the TeX error it was last given |
| Glyph preview: translator, Typst, the latest request per formula | Shell | The UI owns the request counter |
| Selection, drafts, playhead, playback, open panels | UI | |

The **supervisor** does not import Manim or PyAV. It reads `project.json`, writes `scene.py`, starts the Manim process, watches the partial-movie folder, and sends events. Before each render of a project it deletes and recreates `.mathimator/codegen/`, so the folder Manim imports `scene.py` from holds nothing else: a shared project folder cannot plant bytecode or a module there. A read-only file there is made writable and deleted. If Manim fails, including a native Cairo crash, the job ends with an error and the supervisor stays alive. The UI shows the error and keeps the last good frames. A supervisor that dies is restarted by the shell on the next command, as in "Supervisor crash and resync" below; the failed job is not retried.

The **Manim process** is `<python> ... -m mathimator_worker.manim_main render ...` as specified in section 5, which runs Manim's own command line. `<python>` is always a real interpreter. The installed app ships one. When the supervisor itself runs under a venv's `python.exe`, which on Windows is a launcher that starts the real interpreter as its child, Manim runs that real interpreter directly, so every process Manim starts is born inside its Job Object. Manim's config is process-global, so every render is a new interpreter. One Manim process at a time.

**Typst** is started by the shell, not by the supervisor and not by Manim. A newer request for the same formula kills the older Typst process. Typst is never queued behind a scene render.

Frames and movies cross every hop as absolute file paths. They are not base64 on the stdio stream. The webview reads them over Tauri's asset protocol, whose scope the shell widens to each project folder a `state` line names and never narrows, so it covers every project bound in the session until the app quits (D27; a per-project scope is in section 9). The webview's capability grants Tauri's `core:default` and nothing more. The Content Security Policy limits what the page loads to the app itself, the IPC and the asset protocol: previews are fetched over the asset protocol and shown from `blob:` URLs.

### Protocol

This is protocol 2. The supervisor defines `PROTOCOL_VERSION = 2` once, and the shell keeps the same constant.

**Lines.** Each line is one JSON object in UTF-8, ending in a line feed. Numbers are finite: `NaN`, `Infinity` and `-Infinity` make a line invalid JSON, and the supervisor never writes them. A line the supervisor cannot serialize goes out as `error internal` for the same id instead, so no reply is ever a line the shell cannot parse.

A request has `cmd` and `id`, a non-empty string that is unique within one supervisor process. The shell takes every id from one counter: `rpc-N`, `status-N`, `cancel-N`, `tex-N`, `preview-N` or `export-N`. Unknown request fields are ignored. Every supervisor line has `event` and `jobId`: the request's id for a reply, the job's id for a job's lines, and null only for a line that had no usable id.

| Class | Line | Meaning | Afterwards |
| --- | --- | --- | --- |
| Success | The command's result: `ready`, `state` or `code` | Done | Changed as the command says |
| Refusal | `rejected {code, message, field?}` | Refused before anything changed. `message` is written for the user | Nothing changed |
| Failure | `error {code, message, details?}` | An internal failure, a malformed line, or a job that failed or was cancelled | For a request, nothing changed: every change is all or nothing |

- A request whose answer the shell waits for gets exactly one reply line, written before the supervisor reads its next request, so replies arrive in request order.
- `render_preview` and `export` start jobs and get no reply line. All of a job's lines carry its id, and a job ends with exactly one terminal line: `done`, `error` or `rejected`. Nothing follows it.
- `cancel` writes no line when it succeeds; the target job ends with `error cancelled`. `set_tex_error` writes no line.
- An unexpected exception while a request is handled is logged with its traceback and answered at once with `error internal`. The supervisor keeps serving, with its undo history and any running export, and the request's change is rolled back or never applied.
- Within protocol 2 the shell and the UI ignore unknown fields and unknown events.

**Handshake.** The shell starts every supervisor the same way. It spawns it once the previous supervisor has exited, sends `status`, and waits up to 30 s for `ready`, which it keeps to itself. A `ready` whose `protocol` is missing (protocol 1) or is not 2 stops the supervisor, and the app shows "Worker unavailable" with "The worker is out of date (protocol P, this app needs 2). Reinstall Mathimator."; a debug build says "Restart the dev build so it runs the repository worker." instead of the last sentence. A supervisor that exits or does not answer gives "The worker stopped during startup (exit code N). Details are in <logDir>\supervisor.err." or "The worker did not start within 30 s." When the exit code is not known, " (exit code N)" is left out, here and in every message below that carries it. `ready.startupError` is null when a project is bound, and otherwise says why none is (see "Project lifecycle"). Every command that needs the supervisor waits until the handshake is done.

**Commands.** An RPC is a request whose reply the shell waits for.

| cmd | Kind | Fields | Success | Refusal codes |
| --- | --- | --- | --- | --- |
| `status` | RPC | | `ready` | none |
| `get_state` | RPC | | `state` | `no_project` |
| `edit` | RPC | `edit`: an object | `state` with `changed`, and `focus` naming the object or beat the edit is about | `no_project`, `invalid_edit`, `invalid_text`, `unsupported_expression`, `limit_exceeded`, `untrusted_latex`, `project_unavailable`, `read_failed`, `project_changed`, `save_failed` |
| `undo`, `redo` | RPC | | `state` | `no_project`, `nothing_to_undo`, `nothing_to_redo`, `project_unavailable`, `read_failed`, `project_changed`, `save_failed` |
| `code` | RPC | | `code` | `no_project`, `invalid_document` |
| `clear_cache` | RPC | | `state`, with `warning` when a file could not be removed | `no_project`, `render_in_progress`, `project_unavailable`, `linked_folder` |
| `open_project` | RPC | `path`: a folder, or its `project.json`; `init`: a boolean, default false | `state` | `export_running`, `not_a_project`, `linked_folder`, `project_locked`, `lock_failed`, `save_failed` (the `init` write), `read_failed`, and the document codes: `not_utf8`, `invalid_json`, `invalid_text`, `untrusted_latex`, `unsupported_expression`, `limit_exceeded`, `invalid_document` |
| `reload` | RPC | | `state` | `no_project`, `project_unavailable`, `read_failed`, and the document codes |
| `overwrite` | RPC | | `state` | `no_project`, `project_unavailable`, `save_failed` |
| `save_as` | RPC | `path`: a folder | `state` | `no_project`, `export_running`, `save_as_failed` |
| `render_preview` | Job | | Job events | Terminal refusals: `no_project`, `project_unavailable`, `read_failed`, `project_changed`, `linked_folder`, `invalid_document`, `tex_unavailable`, `path_unsupported`. Terminal failures: `cancelled`, `render_failed`, `internal` |
| `export` | Job | `force`: a boolean, default false (Re-export) | Job events | The refusals above and `export_running`. The failures above and `publish_failed` |
| `cancel` | Send | `jobId`: the job to cancel | No line | `no_such_job` |
| `set_tex_error` | Send | `message`: why formulas cannot render, or null when they can | No line | none |

A malformed request gets `error bad_request` with a message for developers: invalid JSON, `NaN` and `Infinity` included; a missing or empty `cmd` or `id`; an unknown `cmd`; or a top-level field that is missing or of the wrong type (`edit` not an object, `path` not a string, `jobId` not a non-empty string, `message` neither a non-empty string nor null, `init` or `force` not a boolean). What is inside `edit` is a document matter: a missing, non-string or unknown `op`, and every bad edit field, give `rejected invalid_edit`. There is no `scrub` command, because playback belongs to the UI, and no `render_frame` command.

**Edit operations.** `edit.op` names the change. Ids are strings, numbers are finite, and colors are `#RRGGBB`, compared without regard to case and stored in uppercase. An unknown, missing or non-string `op` is refused with "Unknown edit operation <op>."

| op | Fields |
| --- | --- |
| `set_project_name` | `name` |
| `set_object_name` | `objectId`, `name` |
| `set_object_color` | `objectId`, `color` |
| `set_expression` | `objectId`, `expression` |
| `set_latex` | `objectId`, `latex` |
| `set_text` | `objectId`, `text` |
| `set_anchor` | `objectId`, `anchor` |
| `set_object_shift` | `objectId`, `shift` as `[dx, dy]` |
| `set_axes_range` | `objectId`, `axis` (`x`, `y`, or `z` in 3D), `value` as `[min, max, step]` |
| `set_length` | `objectId`, `axis` (`x` or `y`), `value` |
| `set_show_labels` | `objectId`, `value` as a boolean |
| `set_domain` | `objectId`, `xMin`, `xMax` |
| `set_beat_duration` | `beatId`, `duration` |
| `set_beat_color` | `beatId`, `color` |
| `set_beat_shift` | `beatId`, `shift` as `[dx, dy]` |
| `add_axes_function`, `add_formula_transform`, `add_three_d_axes` | `afterBeatId`: a beat id or null |
| `add_formula` | `afterBeatId`, `latex` (optional) |
| `add_text` | `afterBeatId`, `text` (optional) |
| `delete_object` | `objectId` |
| `set_three_d_mode` | `mode` (`surface` or `curve`) |
| `set_camera` | `key` (`phi`, `theta` or `zoom`), `value` |
| `set_resolution` | `objectId`, `value` as `[u, v]` integers |
| `set_curve_component` | `objectId`, `index` (0, 1 or 2), `expression` |
| `set_export_profile` | `format`, `width`, `height`, `fps`, `transparent` |

**Events.**

| event | Fields | Sent for |
| --- | --- | --- |
| `ready` | `protocol`: 2; `startupError`: null or `{code, message}` | `status` |
| `state` | Below | `get_state`, `edit`, `undo`, `redo`, `clear_cache`, `open_project`, `reload`, `overwrite`, `save_as` |
| `code` | `source`: the generated `scene.py` | `code` |
| `rejected` | `code`, `message`, and `field` when the refusal is about one field | Any refused request; terminal for a job |
| `error` | `code`, `message`, and `details` when there are more lines | A failed or cancelled job, an internal failure, a malformed line |
| `queued` | `code`: `stopping_previous`; `message`: "Waiting for the previous render to stop." | A job held because the render before it did not stop within 1 s |
| `started` | | A job that has just started Manim |
| `beat_ready` | `beatIndex`, `path` | A preview beat whose partial movie is finished. Exports send none |
| `done` | `path`; for a project's export also `current` | A job that succeeded |

The `state` reply holds the whole document and what is derived from it:

```text
{
  "event": "state",
  "jobId": string,
  "projectPath": string,              // the bound project folder
  "document": object,                 // project.json as a JSON object
  "derived": { "starts": number[], "total": number, "dirty": boolean[],
               "canUndo": boolean, "canRedo": boolean },
  "index": null | {
    "output": string | null,          // the full preview MP4
    "beats": [ { "id": string, "index": integer, "derivedStart": number,
                 "duration": number, "partial": string | null,
                 "lastGood": string | null, "fresh": boolean } ]
  },
  "source": string | null,            // the generated scene.py, which View code shows
  "sourceError"?: string,             // why there is no source; only when source is null
  "focus"?: { "beatId": string | null, "objectId": string | null },   // edit replies only
  "changed"?: boolean,                // edit replies only; false when nothing changed
  "warning"?: { "code": string, "message": string }                   // clear_cache only
}
```

`dirty` is for the preview profile. Every absolute path the supervisor sends, `projectPath` and the `index`, `beat_ready` and `done` paths, is the bound folder's long path as `projectPath` spells it, joined with a path inside that folder. The supervisor never sends a path taken from Manim's log or segment list, or an 8.3 alias, because the webview can read only files under a `projectPath` the shell has seen. The name `code` is kept for status codes: `rejected.code`, `error.code`, `warning.code` and `queued.code`.

**Refusal codes.** A message in quotes is fixed text. The other messages name the field, beat, file or id they are about. `field` is the JSON path from the document root for open and reload, such as `scene.objects[2].params.latex`, and the edit's field name for an edit, such as `expression`.

| code | When | Message |
| --- | --- | --- |
| `no_project` | No project is bound, and the request needs one. Also a hand-written scene that is missing | "No project is open." and why, or "No project is open, and <file> does not exist." |
| `not_a_project` | `open_project` names no folder, or a folder without `project.json` and without `init`. Also the startup error of hand-written mode | "That folder is not a Mathimator project." |
| `project_unavailable` | The bound folder was moved, deleted or replaced, or its `project.json` is gone | "The project folder was moved or deleted." |
| `read_failed` | `project.json` exists but cannot be read: access denied, a sharing violation, an offline cloud placeholder | "Could not read project.json: <reason>." |
| `project_locked` | Another Mathimator window holds the project | "This project is open in another Mathimator window." |
| `lock_failed` | `.mathimator/` or its lock file cannot be created or opened, for example in a read-only folder | "Mathimator cannot write to <folder>: <reason>." |
| `linked_folder` | One of the project's own folders, or its log, is a symbolic link or a junction (section 4), and for a job also its quality folder or the path its export is published as | "Mathimator cannot use <path>: it is a link to another location, and Mathimator keeps its files inside the project. Delete the link and try again." |
| `project_changed` | `project.json` differs from the bytes last read or written, before a write or a render | "project.json was changed outside Mathimator." |
| `save_failed` | Writing `project.json` failed after its retries; the change was rolled back | "Could not save project.json: <reason>. The change was undone." |
| `not_utf8` | `project.json` is not UTF-8 | "project.json must be saved as UTF-8 text." |
| `invalid_json` | A JSON syntax error, nesting too deep, or a number too large | "project.json is not valid JSON: <detail>." |
| `invalid_text` | A string holds a lone surrogate, NUL, or a C0 control other than tab, line feed or carriage return | "<field> contains a character Mathimator cannot store." |
| `untrusted_latex` | Formula LaTeX uses a command outside the allowlist of section 4 | "<field> uses \<command>, which formulas cannot use." |
| `unsupported_expression` | An expression outside the grammar of section 4, longer than 1000 characters, or nested deeper than 64 levels | "This expression is not supported." |
| `limit_exceeded` | An axis over 500 ticks, sampling over its bound, a beat under 0.1 s, a total over 120 s, LaTeX over 500 or text over 200 characters, a resolution over 24×24 | Names the limit |
| `invalid_document` | Any other refusal of a document: its schema, an unknown key, `start`, ids, numbers, the timeline rules, a newer `generatorVersion`. Also a scene the generator refuses, from `code` or at the start of a job | Names the field, beat or id |
| `invalid_edit` | Any other refusal of an edit: an unknown `op`, an unknown id, a wrong object type, a range rule, a 3D template rule, a change to a param its template sets | Names the problem |
| `nothing_to_undo`, `nothing_to_redo` | The history is empty | "Nothing to undo." or "Nothing to redo." |
| `export_running` | An export runs or waits, and the request is `export`, `open_project` or `save_as` | "An export is already running." |
| `render_in_progress` | `clear_cache` while a job runs or waits | "A render is in progress." |
| `tex_unavailable` | A job whose scene needs LaTeX (a formula, or axes with labels) while the shell reports TeX unavailable | The shell's reason |
| `path_unsupported` | The project path cannot render (section 5) | "Mathimator cannot render the project in <folder>: <reason>. Move it to a shorter or plainer path." |
| `save_as_failed` | The Save As target is not a folder, is the open project, already holds `project.json` or `.mathimator`, or the copy failed | "Could not save the project to <folder>: <reason>." |
| `no_such_job` | The `cancel` target neither runs nor waits | "No render with that id is running." |

**Failure codes.**

| code | When | Message and details |
| --- | --- | --- |
| `cancelled` | A job was cancelled, by the user or by a newer request | "cancelled". The UI decides on the code alone |
| `render_failed` | Manim exited non-zero, exited 0 without its output or after using an incomplete partial, or could not start; a PNG sequence's frame count did not match Manim's report or the document (section 7); the old TeX inputs or PNG frames could not be removed; or the run marker could not be written | One line, such as "manim exited with status N: <exception>", "Could not start Manim: <reason>" or "manim used an incomplete clip <file>; it was removed, so render again". A TeX failure is "LaTeX failed." with TeX's `!` line, its `<argument>` line when it shows one, and its `l.N` line. `details` holds the last 80 lines of Manim's output; worker.log keeps all of it |
| `publish_failed` | A finished export could not be copied into `.mathimator/exports/`, failed the preview-file guard, or found a link on its way there (section 4) | "Could not save the export to <file>: <reason>." An operating-system error adds "If the file is open in another program, close it and export again." A link gives the `linked_folder` message |
| `internal` | An unexpected exception, or a line that could not be serialized | "Internal error: <type>: <text>. Details are in worker.log." |
| `bad_request` | A malformed request | For developers |

`clear_cache` may carry the warning `files_in_use`, "Some cache files are in use and were not removed. Close other programs using this project and try again." `queued` carries `stopping_previous`.

### Jobs

The shell hands out job ids, `preview-N` and `export-N`, from one counter per app run. The UI records the id as its current preview or export before it sends the job, so no event arrives for an id it does not know, and it ignores events for any other id.

For one job id the supervisor sends, in this order:

1. At most one `queued`.
2. At most one `started`, when the job starts Manim. A job that ends without Manim sends none: an export found in the export cache, a refusal, or a held job that is cancelled.
3. For a preview, zero or more `beat_ready`, each `beatIndex` at most once, in increasing order.
4. Exactly one terminal line: `done`, `error` or `rejected`.

A preview's `done.path` is the preview MP4. An export's `done.path` is the published file in `.mathimator/exports/<key16>/`, and `done.current` is true when the export key captured at the click is still the current document's. An export found in the export cache sends `done {current: true}` at once.

A preview sends `beat_ready` only while it is not cancelled, belongs to the folder bound now, and renders the document open now, beat for beat. The supervisor checks that, records the partial in the preview index and writes the line in one step under the document lock. An edit, undo, redo, reload, Open or Save As therefore either comes first, and the line is not sent, or comes after, and its reply follows the line on stdout.

**Starting a job.** Holding the document, the supervisor checks, in order:

1. A project is bound (`no_project`), or the folder is in hand-written mode.
2. The write and render checks of "Project lifecycle": the folder (`project_unavailable`), then `project.json` (`read_failed`, `project_changed`).
3. No link where the job writes or deletes (`linked_folder`): the project's own folders and its log, the job's quality folder with its partials folder, and for an export the file or folder it is published as.
4. The scene generates (`invalid_document`).
5. TeX is available when the scene needs it (`tex_unavailable`).
6. The project path can render (`path_unsupported`).
7. For an export without `force`, the export cache, which may answer `done` at once.

Then it writes `scene.py`, builds the command line, starts Manim and sends `started`. Starting a job never writes `project.json`: every edit has saved it already, and a file changed outside Mathimator must not be overwritten.

**Cancel.** `cancel {jobId}` ends a running job by terminating its Job Object or, without one, its process tree, and the job ends with `error cancelled`. A held job leaves its slot and ends with `error cancelled` at once. Any other id gives `rejected no_such_job`. A newer preview, an export, a replaced held preview, and an Open or Save As cancel previews the same way; the protocol does not say who cancelled. Only the newest, possibly cut-off partial is discarded.

**Hand-written mode.** A folder without `project.json`, opened at startup without `--init`, renders the `scene.py` found in `.mathimator/codegen/`, and leaves that folder as it is. Only the phase 1 tests use it. Its `ready.startupError` is `not_a_project`, and every request that needs a document is refused with `no_project`. It takes no lock and runs none of the checks, the link checks included, though its deletes still never follow a link. A preview sends `started`, `beat_ready` lines read from the segment list, and `done`; an export sends `started` and `done` with Manim's own movie, publishes nothing and has no `current`. A folder whose `project.json` was refused is never in this mode: its jobs get `no_project`.

### Progress and the preview index

Before a job, the supervisor copies the newest file of each beat that is not fresh into `cache/last-good/`, and records the size and modification time of every file in this job's quality folder and in its partials folder, `partial_movie_files/MainScene`. Manim finishes one beat before the next, and reuses any partial that already exists. A partial that is new or changed since that record was written by this run; it counts as finished once one with a later modification time exists, and it belongs to the next beat that is not fresh. Partials that share a modification time have no known order, so their beats wait for the segment list. When Manim exits, the segment list it wrote (`partial_movie_file_list.txt` in that folder) gives the partial of every beat in order, including the ones it reused, and settles the index. `beat_ready` goes out in increasing beat order, so a finished beat waits until every beat before it is known. Progress never comes from Manim's log text, which Rich wraps to its own width. The supervisor writes a preview index: beat id, derived start, duration, partial path, and whether that beat is fresh. A file counts as fresh only for the document it was rendered from: the index stores, with each file, a key of the beat and every beat before it, so an undo brings back the beat's earlier file as fresh when that file still exists, and a file from another document never shows without the badge. The keys stay on disk and are not sent to the UI. On disk the index names its files relative to the project folder, so a copied or moved project finds its own media, and a stored path outside the project's media and last-good folders is ignored. Phase 1 checks that a full render yields 8 partials in beat order, and that changing the last beat leaves the first 7 hashes unchanged.

Manim reuses any partial movie that exists, so a cut-off partial would break every later render of its beat. The supervisor records a partial in the preview index, or marks its beat fresh, only when its container is complete. An MP4 or MOV is complete when its top-level boxes add up to exactly the file length, none has size 0 (the placeholder FFmpeg fills in when it closes a file), and one of them is `moov`. A WebM is complete when an EBML header is followed by top-level elements of known size that add up to exactly the file length, one of them a Segment. The check uses the standard library only. Before each render the supervisor deletes the incomplete partials in this job's quality folder, so those beats render again. A render that exits 0 after using an incomplete partial is an error: that partial is deleted, nothing is published, and the next render redoes its beat. A PNG-sequence export writes no partials, so it reads no segment list.

Before it starts Manim, the supervisor writes `.mathimator/cache/manim-run.json`: the job's quality folder, such as `480p15`, its format, and that record of sizes and modification times. It removes the file when the run ends, whatever the outcome; a job that cannot write it fails with `render_failed` before Manim starts. A run that is cancelled or exits non-zero deletes what it wrote in its quality folder that may be cut off: an incomplete partial (only the newest one can be), the segment list and the scene movie. Complete partials stay, so the next render reuses them. Files that were there before the run, and other quality folders, are left alone, and no clock is compared. A PNG-sequence export that is cancelled, fails, or has its frame count refused also deletes Manim's PNG folder, whose sequence may be cut off. A file that cannot be deleted is retried for about 0.5 s and then logged. A quit, a crash or a kill can leave the marker behind. Whenever a folder is bound while the marker exists and no render of that folder is running, the supervisor deletes what the run wrote by the same comparison, and the PNG folder after a PNG-sequence run, then the marker. While a file cannot be deleted, the marker stays, so the next bind tries again. A marker it cannot read, or one whose quality is not `<height>p<fps>`, counts as a run that wrote everything in every quality folder and the PNG folder.

### Profiles

The preview profile is fixed at 854×480, 15 fps, opaque, on the scene background color. It is not a user control in v1. The canvas does not fake a transparent checkerboard.

Export formats and the allowed resolutions (854×480, 1280×720, 1920×1080) and frame rates (15, 30, 60) are in section 7. Preview and export share `cache/media`. A preview-quality partial is never copied into `exports/`. The acceptance test probes the exported frame size.

### Scrub and playback

When every beat is fresh, the canvas plays the MP4 Manim wrote for the preview, and scrub sets that element's time. When any beat is dirty, the canvas shows the partial movie for the beat under the playhead: the new file if that beat is fresh, otherwise the last good file, with the badge "Preview is out of date". If a beat has never had a partial movie, the canvas is blank and the badge is shown.

The beat under the playhead is the last beat whose derived start is at or before it, so a playhead on a boundary is at the next beat's start, as the timeline's highlight shows. The preview is 15 fps, and each beat holds the movie frames from the frame of its start up to the frame of its end, by the frame rule of section 5. Scrub shows the beat's frame `floor(offset × frames / duration)`, with the offset and the duration in whole milliseconds and never past the beat's last frame, in the movie or in the beat's own partial, and seeks to the middle of that frame, because a seek to its edge can show the frame before. While a file plays, the playhead follows it by the inverse mapping and never passes the beat's end or the total. `playbackAt` in `desktop/src/playback.ts` is the one scrub rule; the supervisor has none (D44).

Playback is a timer on those files, owned by the UI. It sends no request to the supervisor and does not call Manim. Over a beat without a file the clock runs on over a blank canvas, the video resumes at the next beat that has a file, and playback stops at the end of the timeline (D49). `cache/last-good/` holds the previous partial for a dirty beat until the replacement partial is ready. Clear cache may delete it. A preview file the webview cannot read is logged to the developer console with its path and HTTP status. It counts as missing only for the preview index it was read for, so the canvas falls back to the last good file or a blank one, and the next `state` or `beat_ready` tries it again.

### Stale preview and export

The status "Rendering export" is shown within 200 ms of the Export click, from the UI, before the supervisor answers. From the click until the export's terminal line, or until the export stops before it is sent, Open, Sample, New and Save As are disabled, and the supervisor refuses `open_project` and `save_as` with `export_running` while an export runs or waits.

A file is the current export only when an `export.json` manifest beside it records the current `exportKey`. If that file already exists, the supervisor returns it and does not start Manim. Otherwise the supervisor runs an export-quality render. Re-export (`export {force: true}`) skips that check and renders again, reusing Manim's cached segments; the older file stays until the new one replaces it. On exit code 0 the supervisor copies the primary output to a temporary name in `.mathimator/exports/<key16>/`, replaces the published file with it, and then writes the manifest the same way. A PNG sequence is a folder, so the old folder is moved aside first and moved back if the new one cannot take its place. The copy runs without holding the document, so edits are answered while it runs. If the file cannot be replaced, for example because a player holds it open, the older file and its manifest stay and the export fails with `publish_failed`: "Could not save the export to <file>: <reason>. If the file is open in another program, close it and export again." Preview files are never copied there; an output that fails that guard also gives `publish_failed`.

If the document changes while an export runs, the export continues for the `exportKey` captured at the click. Typing does not cancel it and does not start a new export. When it finishes, the file is kept for the hash it was rendered from, and `done.current` says whether that hash is still current. When it is not, the UI notes "This file is from an older export." A second export while one runs or waits is refused with `export_running`.

Cancel discards an unfinished export output. A previous finished export for an older hash is left in place and is not labeled current.

### Job queue

At most one Manim grandchild runs. The supervisor keeps the current job and two hold slots, one for an export and one for a preview.

| When the request arrives | `render_preview` | `export` |
| --- | --- | --- |
| Nothing runs | Starts | Starts |
| A preview runs | Cancels it, then starts | Cancels the preview, then starts. The preview is not queued again; the UI asks for one when the export ends |
| An export runs or waits | Waits in the preview slot. An older waiting preview is cancelled | Refused with `export_running` |
| The job before was told to stop and still runs 1 s later | Waits in the preview slot and sends `queued` | Waits in the export slot and sends `queued` |

When the current job ends, the held export starts, otherwise the held preview. A preview held behind a held export waits for it too. Document edits never cancel an export and never start one. Preview cancel uses the same 1 second process-exit criterion as export cancel. A render that never stops holds the next request until it does; the user can cancel the held export or supersede the held preview. A cancelled render keeps reading Manim's output to the end, so Manim never blocks on a full pipe, but it does not wait more than 0.5 s for a child that escaped the kill and still holds that output.

On Windows the supervisor puts each Manim child in a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, using `ctypes`, and cancel terminates the job. `latex` and `dvisvgm` are gone within 1 second. The Tauri shell assigns the supervisor to a kill-on-close job so quitting the app reaps the same tree. When a job cannot be used (`MATHIMATOR_WIN32_JOBS=0`, or assigning or terminating it fails), cancel falls back to a hidden PowerShell helper that lists every process with its parent and creation time. The helper starts with the first render that runs without a job, never at launch. Windows keeps the parent id of a process whose parent has exited and reuses ids, so the fallback counts a process as a child only when it was created no earlier than its parent, and kills each one through a handle that shows it is still the process the list named. A process it could not end is written to the worker log. The helper is the PowerShell script in `process_group.py`; a small C# class inside it makes the fallback's Win32 calls, `CreateToolhelp32Snapshot`, `Process32First`, `Process32Next`, `OpenProcess`, `GetProcessTimes`, `GetExitCodeProcess`, `TerminateProcess` and `CloseHandle`, so the worker makes none of them through `ctypes` (D15). Linux terminates the process group through `subprocess`. macOS is untested. The worker does not use `pywin32`, `win32api`, or `taskkill`. Craig approved this Windows binding; it replaces the earlier rule that forbade every Win32 call. Besides it, the worker calls `GetShortPathNameW` for the 8.3 alias of a project folder, and reads the `LongPathsEnabled` registry value with `winreg`, for the path checks of section 5 (D61).

### Project lifecycle

The supervisor is bound to at most one project folder: at startup, by `open_project` (Open, Sample and New), and by `save_as`. While bound it holds the folder's lock, the SHA-256 of `project.json` as last read or written, and the folder's identity, its volume and file id (section 4). Every bind starts a new binding generation, and a job started under an earlier one records nothing in the folder bound now.

**Write and render checks.** Before a request writes `project.json` or starts a render, the supervisor checks, in order:

1. The folder is still a folder with the identity it was bound with. Otherwise `project_unavailable`.
2. `project.json` exists. Otherwise `project_unavailable`.
3. Its bytes can be read. Otherwise `read_failed`.
4. They hash to the fingerprint. Otherwise `project_changed`.

`get_state`, `code` and an edit that changes nothing write nothing and skip the checks. `overwrite` runs checks 1 and 2, `clear_cache` check 1, and hand-written mode none. A change refused with `project_unavailable` is not applied. The changes saved before it stay in memory, and Save As writes them to another folder.

**Edits.** An edit is checked and applied to a copy of the document. If the saved bytes would not change, the reply is `state {changed: false}`: nothing is written, no undo step is added, Redo is kept, and the checks are skipped. Otherwise the checks run, `project.json` is written atomically (`save_failed` leaves everything as it was), and only then is the change committed: one undo step, Redo cleared, the new fingerprint. The preview index and the render records follow; a record that cannot be written is logged, and the edit still succeeds. Undo and redo do the same with the document from their stack.

**Startup.** The shell starts the supervisor as `<python> -P -m mathimator_worker --project <folder> --python <python> [--init]`; tests may add `--log <file>`. In order:

1. A folder that does not exist leaves the supervisor unbound, with `startupError` `project_unavailable`. Nothing is created, and log lines go to stderr.
2. A folder without `project.json`, started without `--init`, is in hand-written mode.
3. None of the folder's own folders, nor its log, is a link (`linked_folder`), checked before the lock writes there.
4. The supervisor takes the project lock (`project_locked`, `lock_failed`).
5. With `--init` and no `project.json`, it writes an empty project (`save_failed`). Only the shell's new temp folders use `--init`.
6. It reads, decodes and checks `project.json` (`read_failed`, then the document codes).
7. It binds the folder.

A refusal in steps 3 to 6 leaves the supervisor unbound and serving, with the refusal as its `startupError`. It releases the lock, deletes a `project.json` that step 5 wrote, answers `get_state` with `no_project`, and lets `open_project` bind another folder. Startup never exits on a project problem, and it starts no helper process.

**`open_project {path, init}`.** A path to a `project.json` means its folder. In order: `export_running` while an export runs or waits; the folder must exist, and must hold `project.json` unless `init` is true (`not_a_project`, and nothing is created); no link among its own folders (`linked_folder`); the folder's lock (`project_locked`, `lock_failed`), which reopening the bound folder keeps; with `init` and no `project.json`, an empty project (`save_failed`); then the read, decode and check of `project.json` (`read_failed` and the document codes). Only then does the supervisor cancel a held preview and stop a running one, without waiting more than 1 s, release the old lock and bind the folder with a new session, whose undo history is empty. A refusal leaves the old binding, session, undo history and jobs as they were, releases the lock it took, and removes what the attempt created. Open works when the old folder is gone.

**`reload`** reads `project.json` again the same way, replaces the session, so undo history is cleared, and takes the new fingerprint. Jobs keep running; a running preview stops recording partials, because its document no longer matches.

**`overwrite`** writes the document in memory over `project.json` without the fingerprint check, and takes the new fingerprint.

**`save_as {path}`.** The target must be an existing folder other than the bound one that holds neither `project.json` nor `.mathimator`, where a link of either name counts even when it leads nowhere (`save_as_failed`), and Save As is refused with `export_running` while an export runs or waits. The supervisor locks the target, stops previews, writes `project.json`, and copies Manim's media, the last-good files and the exports; a source folder that is gone is skipped. It writes the render records and the preview index afresh, the index naming the copies. On any failure it removes what it wrote in the target and keeps the old binding. Then it binds the target, keeping the session and its undo history. It copies nothing else, not the run marker, the generated code, the glyph files, the logs, the lock or the temp-project marker, and it never changes the old folder.

**`clear_cache`** deletes the derived files described in section 4. It is refused with `render_in_progress` while a job runs or waits, and with `linked_folder` while one of the project's own folders is a link.

**Temp projects.** The shell, not the supervisor, makes temp projects: the launch project when there is no last project to reopen, New, and each Sample copy. Each is a folder `%TEMP%\mathimator-<pid>-<ms>`, where `<ms>` counts up until the name is free. Once the supervisor has bound and locked it, the shell marks it with `.mathimator\temp-project.json`: `{"kind": "mathimator-temp", "seedSha256": "<64 hex>", "createdMs": <unix ms>}`. `seedSha256` is the hash of the folder's `project.json` as first created, and `createdMs` is when the shell wrote the marker. A temp project is touched when its `project.json` no longer has that hash, or its `exports` folder holds a file. For the leave warning and for the deletion after Save As, a folder is a temp folder only when it is named `mathimator-<digits>-<digits>` and lies directly in `%TEMP%`, the paths compared in canonical form, so an 8.3 `%TEMP%` still matches.

The last project is kept in `%APPDATA%\com.mathimator.desktop\state.json` as `{"lastProject": "<folder>"}`. The shell rewrites it whenever a `state` line names a `projectPath` other than the last one it saw, temp projects included, so a temp project the user quit with reopens at the next launch.

At launch the shell starts the supervisor on the last project when that folder still holds `project.json`, and otherwise on a new temp folder with `--init`. When the last project's folder or its `project.json` is missing, the shell adds the notice "Could not reopen <folder>: The folder or its project.json is missing. A new project was started." once a supervisor has started in its place. If a reopened last project gives a `ready.startupError`, the shell opens a new temp project instead and adds the notice "Could not reopen <folder>: <message>. A new project was started.", with the message's own final period dropped. A new launch folder that gives a `startupError` gets no notice: the shell opens another new temp project, deletes the refused folder and logs why. It writes the marker and `state.json` before it releases the commands waiting for the handshake. Pruning and the TeX self-test then run in the background. A launch that starts no supervisor leaves the app on "Worker unavailable" with Retry. A new launch folder that no supervisor took is deleted, so Retry starts on a fresh one, while a last project stays bound and Retry starts on it again.

Pruning deletes the `%TEMP%\mathimator-<pid>-<ms>` folders that are at least 24 hours old, are neither the bound project nor a last project no worker has reopened yet, are not locked, and have no file in `exports`. Folders with other names, such as the TeX self-test's `mathimator-texcheck-<pid>`, are left alone, and pruning never follows a link. A marked folder's age is its `createdMs`, and it must still hash to its seed. An unmarked folder's age is the `<ms>` in its name, and its `project.json` is not checked: it may come from an older build, be bound by a reply that arrived after its request timed out, or have been left unmarked when its marker could not be written. On Windows the shell sees a worker's lock by reading the first byte of the lock file, which Windows refuses while the lock is held, so the check takes no lock and never makes a bind fail. Each deletion is logged in `shell.log`.

New writes an empty project into a new temp folder, and Sample copies the bundled sample into one. Either deletes its folder when the supervisor refuses it, and keeps the folder, unmarked, when the request fails in another way, such as a timeout. Save As asks for a folder ("Save project as"). When it succeeds and the old folder is a marked temp folder that does not hold the new one, the shell deletes the old folder, which makes Save As a move; any other old folder is kept.

Quitting with a touched temp project asks "This project is in a temporary folder that Windows may delete. Save it before quitting?", with Save As…, Quit and Cancel. Open, Sample and New ask "This project is in a temporary folder that Windows may delete. Save it before switching projects?", with Save As…, Don't Save and Cancel; Open asks before its folder picker. Closing either warning is Cancel. Save As… runs Save As, written after the request in flight and before any later one. When quitting, the app quits only when Save As succeeds; a refusal is shown, and the window stays open. Quit keeps the folder, which reopens at the next launch. An unmarked `%TEMP%\mathimator-<pid>-<ms>` folder counts as touched, because pruning may delete it whatever its `project.json` holds. An untouched temp project is left without a question. The touched check waits for the request in flight, so it sees what that request wrote. It starts no supervisor, and while the worker is unavailable nothing asks.

**Supervisor crash and resync.** When the supervisor's stdout ends and the shell did not ask it to stop, the shell fails every request in flight at once with `worker_exited` ("The worker stopped (exit code N)."), logs the exit, and sends the `worker-event` `worker_exited {message, exitCode}`, whose `exitCode` is null when the code is not known. It ends the old process for good, closing its Job Object so that Manim goes too, and starts no supervisor until the old one has exited, so a new supervisor never meets the old one's lock. If the old one still runs 5 s after it was ended, the app shows "Worker unavailable" with "The previous worker did not stop. Choose Retry to try again, or restart Mathimator." Retry ends it again first. The next command starts a supervisor on the bound folder without `--init`. If that one reports a `startupError`, the shell falls back to a new temp project as at launch, with the notice "Could not reopen <folder> after the worker restarted: <message>. A new project was started."

On `worker_exited` the UI ends every job it waits for and resets the Export button, without showing "Cancelled", and asks once for `get_state`. A state with another `projectPath` resets the UI as Open does. Otherwise the UI clears drafts, drops a selection that is gone, and notes "The worker restarted. The document was reloaded from disk and undo history was cleared." No preview starts by itself after a resync.

### The shell and the UI

Every command that needs the supervisor is asynchronous, so the window keeps repainting while it waits, and it waits for the handshake first. A supervisor reply comes back unchanged as the command's value, so a `rejected` line is a value, not an error. The shell's own failures are errors `{code, message}` with the codes `worker_unavailable`, `worker_exited`, `timeout`, `io` and `bad_argument`: an argument the shell checks itself, such as a job id prefix, a glyph slot or generation, or a picked path that is not valid Unicode.

| Tauri command | Arguments | Value | Supervisor request, timeout |
| --- | --- | --- | --- |
| `app_status` | | `AppStatus` | none; waits for the launch's handshake, not for a request in flight |
| `restart_worker` | | `AppStatus` | starts a supervisor when the worker is unavailable, after ending one that did not stop |
| `dismiss_notice` | `id` | null | none |
| `reserve_job_id` | `prefix`: `preview` or `export` | the id | none |
| `get_state` | | `state` or `rejected` | `get_state`, 30 s |
| `edit_document` | `edit` | `state` or `rejected` | `edit`, 30 s |
| `undo_document`, `redo_document` | | `state` or `rejected` | `undo`, `redo`, 30 s |
| `clear_cache` | | `state` or `rejected` | `clear_cache`, 120 s |
| `reload_project`, `overwrite_project` | | `state` or `rejected` | `reload`, `overwrite`, 30 s |
| `new_project` | | `state`, `rejected`, `{cancelled: true}` or `{savedAs: state}` | the leave warning, then `open_project` with `init`, 30 s |
| `open_sample` | | `state`, `rejected`, `{cancelled: true}` or `{savedAs: state}` | the leave warning, then `open_project`, 30 s; `worker_unavailable` without a runtime |
| `open_project_dialog` | | `state`, `rejected`, `{cancelled: true}` or `{savedAs: state}` | the leave warning, a folder picker, then `open_project`, 30 s |
| `save_project_as_dialog` | | `state`, `rejected` or `{cancelled: true}` | a folder picker, then `save_as`, 600 s |
| `export_code_dialog` | | `{path}`, `rejected` or `{cancelled: true}` | `code`, then a save picker, 30 s |
| `render_preview` | `jobId` | null | `render_preview`, sent only |
| `export_movie` | `jobId`, `force` | null | `export`, sent only |
| `cancel_render` | `jobId` | null | `cancel`, sent only |
| `compile_equation_preview` | `latex`, `slot`, `generation` | a glyph result | none; Typst, 10 s |
| `cancel_equation_preview` | `slot`, `generation` | null | none |
| `note_shell_ready` | | null | none; writes the ready marker only when `MATHIMATOR_SHELL_READY_FILE` names a file |
| `third_party_notices` | | the text of the shipped `THIRD_PARTY_NOTICES.txt` | none; reads the file only |
| `open_project_path` | `path` | `state` or `rejected` | `open_project`, 30 s; only in debug builds and builds with the `test-hooks` feature, for the journey tests |

`tests/fixtures/protocol/commands.json` lists every supervisor request with the type of each field, and every Tauri command with its arguments, the supervisor request it sends, and whether only debug and `test-hooks` builds have it. The worker's, the shell's and the UI's tests all check against it. Beside it, `requests.json` holds one request line per supervisor command, in the shell's id order, with the replies each one expects: the worker's tests replay it in process and on the shell's spawn line, and the shell's tests build every line from the shell's `Request` type. `replies.json` records the lines a worker wrote for it, which the UI's parsers read; `uv run pytest tests/test_shell_fixture.py --update-golden` rewrites it. `export-choices.json` gives the export formats, sizes and frame rates, the messages of the opaque formats, and the beat and timeline limits, for the worker's and the UI's tests.

**Dialogs.** One dialog command runs at a time across New, Sample, Open, Save As and Export code; one that arrives while another runs returns `{cancelled: true}`. `{cancelled: true}` also means that no folder or file was picked, or that the user chose Cancel in the leave warning. When Save As in the leave warning moved the project and the command then opened no other project, New, Sample and Open return `{savedAs: state}`, the Save As reply, and the UI follows it as it follows Save As. If that open was refused or failed, rather than cancelled at the picker, the notice "The project was saved, but the next project could not be opened: <reason>." says why. A Save As in the leave warning that is refused returns its refusal. A window close request while a dialog command runs leaves the window open and adds the notice "The window stays open while a dialog or a Save As is still running. Close the window again once that is done." A notice is not added again while one with the same text is open, so repeated close requests show one. A close request while the quit warning runs is ignored without a notice.

**Routing.** A supervisor line whose `jobId` matches a request in flight goes to that request only. Every other line goes to the UI as `worker-event`: job events, the `no_such_job` refusal of a cancel, errors with a null `jobId`, and replies that arrive after their request timed out. A line that is not JSON is only logged. A request that timed out still runs in the supervisor: the UI ignores its late reply, and the `get_state` resync that follows, answered in order, shows its result. The shell takes `projectPath` from every `state` line, claimed or broadcast, for the files the webview may read and for the last project.

**One queue.** The UI sends every request that needs the supervisor through one queue. Tasks run one at a time, in the order they were queued, and apply their result before the next one starts, so the shell writes requests in UI order, the supervisor answers in that order, and the UI applies the answers in that order. The shell holds one request gate from writing a request until its reply or timeout, and it neither reorders nor retries. A failed task never blocks later ones; only an open `project_changed` prompt pauses the queue.

- The Export click shows "Rendering export", disables Open, Sample, New and Save As, and queues the Export task. The task reserves an export id; stops with "Cancelled" if Cancel export was pressed; sends `set_export_profile`, an ordinary edit that is one undo step when the profile changed and a no-op otherwise; stops if that edit is refused; checks Cancel export again; and sends `export` with the Re-export flag. Cancel export queues a cancel of that export, if it was sent.
- The preview task reserves a preview id, records it, and sends `render_preview`.
- Job events reach the UI outside the queue. A preview's `done` queues a `get_state`, and the UI never applies a `state` line from the event channel.
- An applied edit with `changed: true`, an undo, redo or reload, or a state with another `projectPath` makes the current preview stale: the UI ignores its later `beat_ready` lines, and its terminal line still ends it.
- After a timeout the UI says "The worker did not answer in time." and asks once for `get_state`. That resync is single-flight: at most one waits in the queue, and one that fails is reported, not repeated.
- Open, Sample and New, and any other applied state with another `projectPath`, such as a late reply, reset the UI: no selection, no drafts, playhead at 0, playback stopped, View code closed, no field errors, no export path or note. Open, Sample and New also close the Notices.
- Save As, and a `{savedAs}` reply of New, Sample or Open, run no reset: the state names the new folder but holds the same document, so the selection, playhead, playback, open panel, drafts, field errors and note stay, and the shown export path names its copy in the new folder. The current preview still goes stale, as for any state of another folder. Clear cache keeps the export path and note, because it keeps the exports.
- A reply or event the UI cannot read shows "Unexpected reply from the worker: <path> <reason>.", the path starting at the reply or at the event name, such as `state.derived.starts[1]` or `beat_ready.beatIndex`. A rejection from Tauri that is not `{code, message}`, such as its own error for an unknown command, gets the UI's own code `invoke_failed`.

**Refusals in the UI.**

| Code | UI |
| --- | --- |
| `project_changed` | One prompt at a time, with the message and Reload (which discards undo history), Overwrite and Cancel. The rest of the window is inert while it is open, and Escape is Cancel. The queue waits while it is open, and a further `project_changed` joins it. Reload drops the joined requests and previews afterwards. Overwrite retries each joined request once, in order, a job with a new id and an export with its profile and Re-export flag. Cancel drops them |
| `project_unavailable` | A banner with the message and a Save As… button |
| `read_failed`, `lock_failed`, `linked_folder`, `path_unsupported` | A banner with the message |
| `invalid_edit`, `invalid_text`, `unsupported_expression`, `limit_exceeded`, `untrusted_latex` from an edit | A banner, and an error on the edited object's field |
| `tex_unavailable` | A banner; the TeX warning under the status line stays |
| Any other code | A banner with the message |

A banner for an `error` line shows `message` as one line, with Show details for `details` and Dismiss, which clears it for good. After a preview's `render_failed` "manim used an incomplete clip <file>; it was removed, so render again", the banner also offers Render again, which clears it and queues a preview at once.

**Fields, selection and the timeline.** Number fields take plain decimals with a dot or a comma as the decimal point (D24). An empty field, or text that is not a decimal, shows the stored value again and makes no edit, and so does a refused edit. A field error appears under the field the refusal is about, matched on the edit's object id and the refusal's `field`, and for the axes range and length edits, which all name `value`, also on the op and the axis. It goes when the selection changes, and on undo, redo and Open. The selection never names a beat or an object the document no longer holds. A duration drag clamps the duration, to the millisecond, between 0.1 s and 120 s less the other beats. It writes nothing until pointer-up, and the beat keeps its new duration while the edit is applied. A drag interrupted without a release, by a pointer cancel or a lost capture, returns to the duration before the drag and makes no edit (D48). Touch and pen on the handle resize the beat instead of scrolling the timeline. While TeX is blocked, a formula's field says "LaTeX is not working on this PC, so this formula cannot render. Reinstall Mathimator.", and axes that show labels say the same of the axis labels.

**Status line.**

| Situation | Status |
| --- | --- |
| No job | "Ready" |
| A preview is asked for, has started, or has sent a beat, and no export runs | "Rendering preview", then "Rendering preview — beat N", N counted from 1, for each beat of a preview that is not stale |
| Export clicked or its `started` | "Rendering export" |
| `queued` for the export, or for the preview when there is no export | "Waiting for the previous render to stop" |
| Export `done` | "Ready" and the file's path, with "This file is from an older export." when `current` is false |
| An export cancelled by Cancel export, before or after it was sent | "Cancelled" |
| Any other `error cancelled` | Unchanged while an export or a newer preview runs, otherwise "Ready" |
| Any other `error` or `rejected` of the current job, a refused `set_export_profile` before an export, or a job request the shell could not send | "Ready", and the banner or prompt for its code |
| `worker_exited` | "Ready" after the resync |

**Automatic previews.** The UI asks for a preview, after the same 300 ms debounce as edits, after Open, Sample, New, Reload and Clear cache, and after the terminal line of an export that pre-empted a preview, when no preview is current or held. It never asks after undo or redo, for an edit with `changed: false`, for a scene without beats, or after a resync. Unchanged beats reuse Manim's partial cache.

**Startup and AppStatus.** Setup never fails on the runtime, the spawn or the handshake, so the window always opens.

```text
AppStatus = {
  "worker": "ready" | "unavailable",
  "reason": string | null,
  "tex": { "state": "checking" | "ok" | "failed", "message": string | null },
  "notices": [ { "id": integer, "message": string } ]
}
```

At mount the UI subscribes to `worker-event`, calls `app_status`, and shows each notice once; a notice stays until `dismiss_notice` removes it. With a ready worker it asks for `get_state`. With an unavailable one it shows "Worker unavailable", the reason, and Retry, which calls `restart_worker`; editing, Open, Sample, New, Save As and Export stay disabled, and the third-party notices stay available. A later `worker_unavailable` from any command shows the same. An `app_status` or `restart_worker` reply whose TeX state is `checking` never replaces a final result the UI already has from `tex_status`. The shell's own `worker-event` lines have `jobId: null`: `worker_exited {message, exitCode}`, `notice {id, message}` for a notice added after startup, and `tex_status {state, message}` when the TeX self-test ends.

**Runtime and spawn.** A debug build runs the repository's worker with `MATHIMATOR_PYTHON` or the repository's `.venv`, and takes Typst and TinyTeX from `MATHIMATOR_TYPST` and `MATHIMATOR_TINYTEX_BIN`, else from `packaging/bundle` as its `runtime.json` names them, else from PATH. A release build reads only the bundle: `<resources>/runtime/runtime.json` gives `platform` (`windows-x86_64`), `python` (the real interpreter, D33), `worker`, the folder that holds `mathimator_worker`, and `sample`, all required, and `typst` and `tinytexBin`, which only disable the glyph preview or block TeX when missing. A path counts as missing when its field is absent or names nothing in the bundle. The other fields, which section 8 lists, are only logged, and `shell.log` gets `runtime.json` on one line. A missing or wrong runtime, a missing `runtime.json` or resource folder included, gives "Worker unavailable" with "The installed runtime is incomplete (<field>). Reinstall Mathimator."; a missing Typst gives "The bundled Typst is missing. Reinstall Mathimator." as the glyph preview's reason.

The supervisor runs with `PYTHONPATH` set to the worker folder, unbuffered UTF-8 stdio (`PYTHONUNBUFFERED`, `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1`), TinyTeX first on PATH, and `MATHIMATOR_TEX_ERROR` while TeX is blocked. Every inherited `PYTHON*` variable is removed, `PYTHONHOME` and `PYTHONSTARTUP` among them, and so are `TEXINPUTS` and every `TEXMF*` variable. A release build also removes every inherited `MATHIMATOR_*` variable before it sets `MATHIMATOR_TEX_ERROR` itself, so no development switch such as `MATHIMATOR_WIN32_JOBS=0` reaches an installed worker (D30). It reads only `MATHIMATOR_SHELL_READY_FILE` and names each other `MATHIMATOR_*` variable it was started with in `shell.log`: "Ignored <name>: release builds neither read it nor pass it to the worker." The TeX self-test runs `latex` and `dvisvgm` with the same environment. The supervisor starts in a folder the shell owns, `%LOCALAPPDATA%\com.mathimator.desktop` in a release build and the repository in a debug build, without a console window, inside a kill-on-close Job Object. Only a debug build lets `MATHIMATOR_WIN32_JOBS=0` leave it outside one, and a failed assignment is logged in `shell.log`. When a debug build's Python is a Windows venv launcher, the shell starts the real interpreter that the venv's `pyvenv.cfg` `home` names instead, with `__PYVENV_LAUNCHER__` naming the launcher so that the venv still applies and the worker still gets the launcher as `--python`. The process in the Job Object is then the supervisor itself, as it is for Manim (D16).

**TeX.** A release build without its bundled TinyTeX blocks formulas and axis labels: the TeX state is failed with "Formulas and axis labels cannot render: the bundled TeX is missing. Reinstall Mathimator." At every launch where TeX is not blocked already, the shell also runs a TeX self-test in the background once the launch's handshake is done, in a fresh `%TEMP%\mathimator-texcheck-<pid>` folder: `latex` and `dvisvgm --no-fonts` on a one-line formula with `amsmath` and `amssymb`, with hidden consoles and 30 s in all. A failed self-test blocks formulas and axis labels the same way, with "Formulas and axis labels cannot render on this PC: <first `!` line or reason>. Reinstall Mathimator for all users.", until a later check passes. While TeX is blocked, a job whose scene needs it is refused with `tex_unavailable` and that message, and the UI shows the failed state as a persistent warning under the status line. The supervisor learns the TeX state from the shell: every supervisor the shell starts gets `MATHIMATOR_TEX_ERROR` while TeX is blocked, and when the self-test ends the shell sends `set_tex_error` to the running one, with the failure message or null. The supervisor applies it to the jobs that start afterwards. A running job goes on, the document keeps its undo history, and the supervisor is not restarted.

**Logs.** `shell.log` and `supervisor.err` are in the log folder: `<repo>/out/logs` in a debug build, `%LOCALAPPDATA%\com.mathimator.desktop\logs` in a release build, or `%TEMP%\Mathimator\logs` when Tauri cannot name that folder. `shell.log` records the runtime paths, each spawn, each exit with its exit code, a deliberate stop as "Stopped the worker (exit code N).", a protocol mismatch, Job Object failures, the TeX self-test, prune and Save As deletions, the `MATHIMATOR_*` variables a release build ignored, and supervisor lines that are not JSON. `supervisor.err` appends the supervisor's stderr under a header for each start, and moves to `supervisor.err.1` above 1 MiB; a move that fails, for example while another program holds the file, is logged in `shell.log`, and the file is appended to anyway. `worker.log` stays in the project (section 4).

**View code, colors and export settings.** View code shows the latest applied `state.source`, or `sourceError` when there is no source, so it follows every edit, undo and redo without a request of its own. Export code asks for `code`, which comes from the same document, so the two match. The color picker commits once, when it closes, so one use is one edit and one undo step; the hex field commits on blur. `set_export_profile` is sent before every export (above).

**Notices.** The Notices panel shows a short summary, then the text `third_party_notices` returns in an area that scrolls on its own and can be selected. The summary says that Mathimator is MIT and names the main third-party parts: PyAV with FFmpeg's libraries (LGPL-3.0-or-later), among them the x264 and x265 encoders (GPL-2.0-or-later) and LAME (LGPL-2.0-or-later); pycairo (LGPL-2.1 or MPL-1.1); Typst, whose LICENSE and NOTICE are in `runtime\typst`; TinyTeX; and WebView2. It says the licence texts that ship with these parts and the source offer are in `runtime\THIRD_PARTY_NOTICES.txt` in the install folder. The shell reads the file each time the panel opens. A release build reads it from the installed runtime, even when the worker is unavailable, and without it answers `io` with "The installed runtime is incomplete (THIRD_PARTY_NOTICES.txt). Reinstall Mathimator." A debug build reads `packaging/bundle/THIRD_PARTY_NOTICES.txt` and, when it is missing, answers `io` with a message saying the notices are not built. The panel shows that message in place of the file.

### Equation preview

The stored formula is LaTeX. After a 100 ms debounce, the shell translates the supported subset to Typst math and runs Typst 0.15.0 `compile --ignore-system-fonts formula-<slot>-<generation>.typ formula-<slot>-<generation>.svg` in `.mathimator/equation/`. That folder is Typst's working folder and both paths are bare file names, so `{p}`, `{0p}` or `{n}` in a project path is never read as a page template. Typst runs with no console and a 10 s limit ("typst compile timed out"), and its embedded fonts give the same glyph on every PC. The shell runs `typst --version` hidden before the first compile, and keeps the result only once it has passed. The translator is part of the shell, in process (D28); `equation.py` in the worker is the reference the tests compare it with.

The generated Typst document is:

```typst
#set page(width: auto, height: auto, margin: 8pt, fill: none)
$ y = sin(x) $
```

| LaTeX | Typst math |
| --- | --- |
| `\sin` `\cos` `\tan` `\log` `\exp` | the name without the backslash |
| `\pi` `\theta` `\alpha` `\beta` `\gamma` | `pi` `theta` `alpha` `beta` `gamma` |
| `\frac{a}{b}` | `frac(a, b)` |
| `\sqrt{x}` | `sqrt(x)` |
| `^`, `_`, `+ - = ( ) [ ]` | passed through |
| ASCII letters and digits | passed through, each as its own atom: a space separates it from a neighboring letter, digit or command name, so `E = mc^2` becomes `E = m c^2` and `\sin\theta` becomes `sin theta` |

`\frac` and `\sqrt` groups nested more than 100 deep give no preview. That bound only keeps the translator's recursion short: a stored formula of at most 500 characters cannot nest so deep.

`tests/fixtures/equation/translate-cases.json` holds the expected Typst math for each test formula, with `null` where there is no preview. The shell's translator and the worker's reference both read it.

Each request names its formula (`slot`, the object id) and a `generation` from one counter in the UI that only grows. A request older than the newest one seen for its slot, compile or cancel, comes back `cancelled` at once; a newer one kills the slot's older Typst. Two formulas on screen together use different slots. A page reload resets the newest generations, because the UI's counter starts again at 1. The UI asks once more when its current request comes back `cancelled`. A compile returns `ok` with the SVG path, `unsupported`, `error` with a message, or `cancelled`; success deletes the slot's other formula files. The shell makes `.mathimator/equation` inside the project folder, never the project folder itself. A missing project folder gives "The project folder was moved or deleted.", a link in place of `.mathimator` or `equation` gives the `linked_folder` message of section 4, a missing Typst gives "Typst is unavailable: <reason>", and no bound project gives "No project is open."

`\int` and any other command produce no SVG. The editor shows "No live preview for this formula." The scene can still render that formula with `MathTex`. A failed Typst run keeps the last good SVG and shows the error. The caption "Live preview is Typst. The video uses LaTeX and will look different." stays visible whenever a formula is selected.

Tectonic is not on the render path. Final LaTeX uses the bundled `latex` and `dvisvgm`. If a formula needs a package that was not preseeded, the render fails; it does not download the package. A LaTeX failure shows "LaTeX failed." followed by TeX's `!` error line, the `<argument>` line that ends at the command TeX stopped on when TeX shows one, and the `l.N` line that names where TeX stopped, all three read from the TeX log (D11). MathTex sets a formula in `align*`, so for a mistake in a formula the `l.N` line is `\end{align*}`, and the `<argument>` line, such as `<argument>  y = \sinn`, names the bad command. A failure counts as LaTeX only when Manim's own TeX messages say so, not because a path or a formula mentions LaTeX. Any other render failure shows one line, "manim exited with status N: <exception>", with the last 80 lines of Manim's output as details; the full output stays in worker.log.

## 7. v1 features and acceptance criteria

### Live preview, scrub, and partial re-render

Pass when all of these hold:

- A finished `reference-60` preview scrubs to the correct beat's frame within 100 ms and starts no Manim process.
- The partial-rerender cases in section 2 meet the 3 second budget, and earlier partial-movie hashes are unchanged.
- Editing beat `i` marks exactly beats `i..n` dirty before the render starts.
- On `lesson-60`, editing `eq_2` dirties only the transform and the beats after it. Editing the axes dirties beat 0 onward.
- The canvas shows "Preview is out of date" while any beat is dirty and keeps the last good frame.
- Inserting `axes_function` into an empty scene meets the 8 second budget.

### Equation editor

The split a reviewer can reject:

> The formula field stores LaTeX. The live glyph is a Typst approximation of a subset, via the Typst 0.15.0 binary. The scene and every export use `MathTex` and LaTeX, so the video uses Computer Modern. The two pictures are not expected to match. Tectonic is not used.

Pass when all of these hold:

- `y = \sin(x)` shows an SVG within the equation budget, and Manim does not start for that SVG.
- `\int_0^1 x dx` shows "No live preview for this formula." and still appears in a scene preview through `MathTex`.
- Generated scene source for a formula contains `MathTex` and does not contain `TypstMath` or `Typst(`.
- The caption in section 6 is visible while a formula is selected.

### Template library

The v1 templates are `axes_function`, `formula_transform`, and `three_d_axes`, as specified in section 4. Reusable means: insert, then edit the saved parameters, which are stored in the document. It does not mean plugins.

Pass when all of these hold:

- Each template inserts the objects, beats, and defaults in section 4.
- Changing the `axes_function` expression to `cos(x)` updates the plot object and the generated lambda, and leaves beat durations as they were.
- Renaming an object's display name does not change the generated Python.
- The 3D template generates `ThreeDScene`, `set_camera_orientation`, and `--renderer cairo` on the command line. Generated source does not contain `begin_ambient_camera_rotation`, `manimlib`, or `opengl`.
- Inserting the 3D template into a non-empty project is refused. Inserting Axes and function into a 3D project is refused.
- The default 3D surface meets the 20 second insert budget on the reference machine.

### Export presets

| Preset | Default | Transparency | CLI |
| --- | --- | --- | --- |
| MP4 | 1920×1080, 30 fps | Unavailable. "MP4 cannot store transparency." Manim 0.22 rejects transparent MP4. | `--format mp4` |
| GIF | 854×480, 15 fps | Unavailable. "GIF cannot store an alpha channel." Manim can write a one-bit transparent GIF; v1 does not offer it. | `--format gif` |
| WebM | 1920×1080, 30 fps | Optional, including the background. | `--format webm`, plus `--transparent` when the control is on |
| PNG sequence | 1920×1080, 30 fps | Optional, including the background. One PNG per frame, the held frames of a wait included. | `--format png-sequence`, plus `--transparent` when the control is on |

Manim writes every PNG sequence to one folder, `images/<scene>/MainScene`, and never empties it. A PNG-sequence export deletes that folder before Manim starts; if it cannot, the export fails. It publishes the frames only when their number equals both the count Manim reports, which guards against frames left in the folder, and the document's frame count at the export frame rate: the total in whole milliseconds, rounded to the nearest frame with halves up, as for every beat boundary (section 5). A sequence that is short or long, or whose count Manim did not report, fails with `render_failed`, and the folder is deleted, as after a cancel or a failed render.

Resolutions are 854×480, 1280×720, and 1920×1080. Frame rates are 15, 30, and 60. The 3 minute budget applies to `reference-60` at 1920×1080, 30 fps, MP4, cold export cache. Other combinations must finish and match their profile; they do not inherit that time limit.

When transparency is on, a sample background pixel has alpha 0. Object pixels for the default surface stay opaque. The preview stays opaque. A WebM keeps its alpha in a separate stream that FFmpeg's own vp9 decoder ignores, so the check decodes it with libvpx-vp9, as WebView2 does, and also requires the track's `alpha_mode` tag.

Pass when all of these hold:

- The progress label appears within 200 ms, and cancel meets the 1 second test.
- A 1080p30 MP4 of `reference-60` is within one frame of 60 seconds. Over 270 seconds revises this spec.
- `fractional-10`, twenty beats of 0.5 s, exports at 1080p30 as exactly 300 frames, so fractional beats follow the frame rule of section 5.
- The export file's frame size equals the export profile. A preview-sized file fails.
- MP4 and GIF cannot enable transparency. WebM and PNG sequence can, and the alpha check passes.
- A second export of an unchanged `exportKey` does not start Manim.
- Export code writes the same Python as View code, and that text matches the generator. The file does not contain a resolution or an fps.

### Save, open, undo, and offline launch

Pass when all of these hold:

- A committed edit is flushed to `project.json` before the preview job starts, once the project has a folder. Reopening shows that edit. Undo history is empty after reopen.
- Undo and redo restore document edits within 50 ms. Dirty flags are recomputed and are absent from the file.
- An edit that changes nothing writes nothing, adds no undo step and keeps Redo.
- A save that fails leaves the document and the undo history as they were. A `project.json` changed outside the app is not overwritten until the user chooses Overwrite. A moved or deleted project folder is never recreated.
- When `project.json` changed outside the app, the next write or render shows one prompt with Reload, Overwrite and Cancel. Reload discards undo history and starts a preview, Overwrite saves this window's document and retries the refused requests in order, and Cancel writes nothing. A moved or deleted project folder shows a banner with Save As….
- A second window cannot open a project that is already open.
- New starts an empty temp project. Save As copies the document, the render cache and the exports into an empty folder, works on the copy from then on, and keeps undo history and the view. The old folder is not changed, unless it is a temp project, which Save As moves.
- New, Open, Save As and Sample are disabled from the Export click until the export ends. Open, Sample and New first offer to save a touched temp project, and so does quitting.
- A project whose own folders include a symbolic link or a junction is refused, and no delete in a project follows a link.
- A file with `schemaVersion` other than 1, a `start` field, or an unknown key is refused.
- On Windows, cancel and quit leave no Manim, `latex`, or `dvisvgm` process within 1 second. Each Manim child sits in a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and the supervisor itself is in a kill-on-close job. The binding is `ctypes`. The worker package contains no `pywin32`, `win32api`, or `taskkill`. Linux uses the process-group path. macOS is untested.
- After the phase 5 installer exists: on a clean Windows 11 account with no Python, FFmpeg, TeX, or Typst installed, and with the network disconnected, the app installs, launches, opens the bundled `reference-60` sample, and exports a 1080p30 MP4.

The phase 5 report records Python runtime size, TinyTeX size, Typst size, WebView2 size, and installer size. There is no pass/fail size in this spec. The expected toolchain range before tuning is several hundred MB to about 1 GB, plus about 127 MB for the embedded WebView2 offline installer.

### Tests by phase

Automated tests are pytest for the worker, the document, codegen, the scripts and the CI files; vitest for the UI, its rule modules, hooks and components in jsdom; and cargo test for the shell. Scripted UI checks on the reference machine cover the latency budgets. The worker, the shell and the UI check one set of shared fixtures, so a change one side makes and another does not follow fails a test:

- `tests/fixtures/protocol/`: `commands.json`, `requests.json`, `replies.json` and `export-choices.json`, as section 6 describes them.
- `tests/fixtures/timing/frame-boundaries.json`: beat boundaries and the frames they fall on, for codegen's `_beat_time` and the UI's scrub (section 5).
- `tests/fixtures/equation/translate-cases.json`: the Typst math of each test formula, for the shell's translator and the worker's reference.

pytest markers sort out the slow tiers: `render` and `export` run real Manim, `offline` a built bundle with the network blocked, `typst` the Typst binary, and `process_table` the process table. Plain `pytest` runs every tier (D36). The Job Object tests run only with `MATHIMATOR_WIN32_TESTS=1`, and the shell's tests that start the repository's Python, the real worker among them, only with `MATHIMATOR_REAL_WORKER_TESTS=1`. Coverage of the worker and the UI is reported, never enforced (D78). README.md lists the commands of each tier and the CI job that runs it.

| Phase | Tests that must exist |
| --- | --- |
| 1 | Worker renders `reference-60` to 8 partial movies, in beat order, and a preview MP4 within one frame of 60 s. `fractional-10` previews as 150 frames, its beats 8 and 7 frames in turn. Changing the last beat and rendering again leaves the first 7 partial-movie hashes unchanged. A scene that raises `RuntimeError` returns an error, and a following `status` succeeds. Cancel ends the grandchild and any TeX child within 1 s. The argv contains `--renderer cairo` and `--seed`, and does not contain `opengl`, `--live-preview`, or `--preview`. |
| 2 | Byte-identical codegen for the Journey A block and for `reference-60`. Prefix stability: editing the last beat does not change the earlier source. Invalidation on `lesson-60` as in section 5. Derived times, refusal of a stored `start`, save/open, undo/redo, display-name stability, scrub that sends no request to the worker, expression rejection of `__import__("os")`. |
| 3 | Translator cases in `tests/fixtures/equation/translate-cases.json` for every supported command, for `\int`, and for groups nested past the 100-level bound. Equation budget on `y = \sin(x)`. Generated source uses `MathTex` only. An unsupported formula still renders in the scene. |
| 4 | All three templates insert the specified objects and beats. 3D source and argv match section 5 for surface and curve. Refusal rules for mixed 2D/3D projects. 20 second insert measurement. |
| 5 | Export matrix in section 7, including alpha checks and the ban on upscaling. `typst --version` is 0.15.0. With the network disconnected, the bundled interpreter imports `manim`, and `latex` and `dvisvgm` run. The clean-machine install test. `runtime.json` names the bundled worker, the SHA-256 of this checkout's `uv.lock` and the Python the build chose, and Typst's `LICENSE` and `NOTICE` ship beside it. The generated notices name CPython and its native libraries, every bundled Python distribution, PyAV's FFmpeg libraries with x264, x265 and LAME and the written source offer, pycairo, Typst, TinyTeX and its TeX Live packages, the Rust crates, the npm runtime packages and WebView2, and the bundle check refuses notices written for other lockfiles. The Notices panel shows them. |

## 8. Phased build plan

Each phase is a vertical slice. Components named in a phase are the ones that phase introduces. Sample movies are shown at the gate and are not committed as binaries.

Every phase uses this gate: launch the app, run this phase's tests, render the sample scene, export the sample scene, put the required numbers in the phase report, and stop for approval. Do not start the next phase in the same change. If a 1.5× revise rule trips, revise this spec before adding features.

### Phase 1 — Walking skeleton

In: a Tauri 2.12 window with a React + Vite UI; the supervisor and one Manim grandchild; a handwritten `reference-60` scene file that matches section 5; a player for that preview MP4; one export action that writes a 1080p30 MP4. The MIT `LICENSE` file for Mathimator's own source is added in this phase, because approving this spec confirms MIT. Record the exact Tauri patch and the React and Vite versions, and stay on those majors afterward. Tauri stays on 2.12.x.

Absent: timeline, document model, equation editor, templates, GIF, WebM, PNG sequence, installer, Typst.

Tests: the phase 1 row in section 7. Launch the app and record the dev-build cold start. If that second start already exceeds 8 seconds, stop, because the installer will not remove that cost.

Sample: `reference-60`. Render the preview and export the 1080p30 MP4.

Report: preview seconds, export seconds, cold-start seconds, machine description, and whether each Manim time is inside the target, inside 1.5×, or over 1.5×. Over 67.5 s or 270 s revises this spec.

Gate: launch the app, run this phase's tests, render the sample scene, export the sample scene, and stop for approval.

### Phase 2 — Document, codegen, timeline, scrub

In: `project.json` read and write; the document model and validation; codegen; the timeline and inspector; duration, color, position, and a formula string field that becomes `MathTex`; the `axes_function` template; text and formula objects; partial-movie index; scrub; dirty states; undo and redo; View code and Export code. Invalidation tests load a `lesson-60` fixture from disk. The Add menu grows `formula_transform` in phase 4.

Absent: Typst glyph preview, formula-transform template, 3D template, GIF, WebM, PNG sequence, installer.

Tests: the phase 2 row. Re-render `reference-60` through codegen. The preview time must be within 1.5× of the phase 1 preview time and within 67.5 s. Run the 50 ms, 100 ms, 3 s, 8 s, and 3 s-open checks on the reference machine.

Sample: `reference-60` exported as 1080p30 MP4 from the generated file, and Journey A played from the timeline without opening code.

Gate: launch the app, run this phase's tests, render the sample scene, export the sample scene, and stop for approval.

### Phase 3 — Equation editor

In: the LaTeX field, the subset translator, the Typst 0.15.0 process, the caption, and the unsupported-formula state. Final frames still use `MathTex`. This phase may use a developer TeX install. Bundled TinyTeX is phase 5.

Absent: the transform and 3D templates, the export-preset matrix, the installer.

Tests: the phase 3 row, plus the phase 2 tests still passing.

Sample: Journey A, with the Typst SVG visible for `y = \sin(x)`, exported as a 1080p30 MP4 whose frames were produced by `MathTex`.

Gate: launch the app, run this phase's tests, render the sample scene, export the sample scene, and stop for approval.

### Phase 4 — Template library and Cairo 3D

In: `formula_transform`, `three_d_axes`, camera fields, surface and curve mode, the 24×24 resolution cap, and the mixed-scene refusals. Journey C's export may be invoked through the worker before the preset UI exists.

Absent: the full preset matrix, GIF, the installer, offline bundling.

Tests: the phase 4 row, plus the earlier rows still passing. Record the 3D insert time. Over 30 seconds revises this spec before phase 5.

Sample: the default 3D surface, previewed in the canvas and exported as a transparent PNG sequence at 1280×720, 30 fps. Also render the curve mode at the preview profile.

Gate: launch the app, run this phase's tests, render the sample scene, export the sample scene, and stop for approval.

### Phase 5 — Presets and the Windows installer

In: the export preset UI; resolution, fps, and transparency rules; third-party notices; the bundled CPython with the render group of `uv.lock`, pinned as in section 1; preseeded TinyTeX with network installs disabled; the Typst 0.15.0 binary; an offline Tauri Windows installer for all users that embeds the WebView2 offline installer; the `reference-60` project bundled as a sample. The installer is unsigned. A SmartScreen warning on first launch is expected.

Absent: everything in section 9. In particular, no Mac or Linux installer, no separate FFmpeg program besides the libraries PyAV ships, unless a preset test has already failed and this spec has been revised, and no code signing.

TinyTeX is preseeded from the dated TeX Live snapshot below with the packages imported by Manim 0.22's default `MathTex` template, including what that template needs for `amsmath`, `amssymb`, and `dvisvgm`. The offline render is the proof. A missing package is added to the seed list before any other phase 5 work continues. If `manim[typst]==0.22.0` has no wheel for 3.14.9, the environment uses 3.13.17. If it has no wheel for either, stop. Do not change the Manim pin.

Tests: the phase 5 row, plus the full section 2 budget suite on the reference machine against the installed app. The clean-machine VM proves offline install and a finished export. It does not replace the reference machine for the time budgets unless it matches the reference profile.

Sample: `lesson-60` exported as a GIF, and `reference-60` exported as a 1080p30 MP4 from the installed app on the clean machine.

Gate: launch the app, run this phase's tests, render the sample scene, export the sample scene, and stop for approval.

### The installer's runtime

`packaging/build-bundle.ps1` builds, in `packaging/bundle`, the runtime the installer ships as `runtime\`, and `npm run tauri build` makes the installer from it. README.md gives the steps.

- `uv.lock` is the single source of the Python render stack for development, CI and the bundle (D34). The build installs its render group into the bundled interpreter with the hashes it records, from wheels only, except a package that publishes no wheel at all (today `srt`), which is built with the setuptools that `uv.lock` pins. When a locked package has no wheel for CPython 3.14.9, the build uses 3.13.17 and records why.
- `packaging/versions.env` pins uv 0.13.5, which in turn pins the python-build-standalone CPython builds it installs and checks; CPython 3.14.9 and 3.13.17; TinyTeX 2026.10; the TeX Live snapshot `https://texlive.info/tlnet-archive/2026/10/23/tlnet`; and Typst 0.15.0. The uv, TinyTeX and Typst downloads for Windows and for Linux each have a SHA-256 there, checked after the download and again when a cached copy is reused, and a build stops at a download without one (D35).
- tlmgr installs the seed packages from that snapshot, so a rebuild gets the same package revisions. Then it is pointed at `https://offline.invalid/tlnet`, a name that never resolves, so TinyTeX cannot download a package, and the build checks that an install fails.
- The bundled interpreter, started with `-I`, must import Manim entirely from inside the bundle.
- The installer puts the runtime under Program Files, where Python run by a standard user cannot save the bytecode it compiles. So the build compiles the bundled Python and the worker with `compileall`, in one process so that every build gives the same bytes, as checked-hash bytecode that records each source as `runtime\...` rather than by its build path. Python checks each `.pyc` against the hash of its source on import, so a stale one never runs in its place.
- `scripts/check_bundle.py --prune` then removes the top-level entries outside `python`, `tinytex`, `typst`, `worker`, `samples`, `runtime.json`, `THIRD_PARTY_NOTICES.txt` and `.gitkeep`, such as a crash dump or an older build's venv (D64), and everything in a `__pycache__` folder but the bytecode the build compiled. It removes a link itself and never enters one.
- `runtime.json` is written last, once the bundle has passed: the fields section 6 reads, and `pythonVersion`, `pythonSelection`, `manim`, `lockSha256` (the SHA-256 of `uv.lock`), `typstVersion`, `tinytexVersion`, `tinytexRelease` and `tlnetRepository`. The build logs the size of each top-level entry and of the whole bundle.
- On Windows, `tauri build` runs `scripts/generate_notices.py` and then `scripts/check_bundle.py` on the finished bundle before it makes the installer. The check fails unless `runtime.json` is for this platform, its paths lie inside the bundle and `python` is the real interpreter, not a venv launcher; the worker and the sample equal their sources byte for byte; `lockSha256` and the versions match `uv.lock` and `packaging/versions.env`; the notices name the SHA-256 of this checkout's `uv.lock`, `Cargo.lock` and `package-lock.json`; and nothing lies outside the layout. Both runs of the check fail on a link that leaves the bundle, and on a file, bytecode included, that names the checkout or a path in the builder's home folder.

`packaging/build-bundle.sh` builds a Linux runtime for the tests. There is no Linux installer, and `generate_notices.py` refuses a Linux bundle, because its tables list only the libraries the Windows CPython links in.

### Versions, CI and releases

README.md and CONTRIBUTING.md hold the commands, the job list and the release steps. They follow these rules:

- One command, bump-my-version, changes the app version in every manifest. `scripts/check_versions.py` fails CI when a manifest, a lockfile or a copy of the Manim, Typst, CPython, uv, Rust or Node pin disagrees, and when a release tag is not `v` followed by the app version. `CHANGELOG.md` and the release notes are generated with git-cliff from Conventional Commits (D72).
- The versions this spec pins change only by hand and together: Manim 0.22.0, Typst 0.15.0, CPython 3.14.9 with 3.13.17, uv 0.13.5, Rust 1.93.0, Node 24, Tauri 2.12.x, React 19 and Vite 8 (D37). Renovate proposes other dependency updates and none of these (D76).
- The pipeline runs on gitlab.com's Free tier and its hosted Linux and Windows runners (D66, D67). Its compute minutes are few, so a merge request runs the version and lint checks, SAST and Secret Detection, and only the lanes whose files it changes, and after a merge the default branch runs only those checks and scans again. The Windows Rust build, the render, export and offline suites and the installer run on tags and when someone starts them by hand. There is no nightly pipeline: the owner revised D68 and D73 on 24 October 2026. cargo-deny, npm audit and pip-audit check the lockfiles (D77), and pre-commit hooks run the lint checks locally (D71).
- A protected `vX.Y.Z` tag runs every job, builds the installer, uploads it with `THIRD_PARTY_NOTICES.txt` and `SHA256SUMS` to the project's package registry, and creates a GitLab Release with the git-cliff notes (D73).
- v1 installers are unsigned, with the signing hook in place but off: the CI/CD variable `WINDOWS_SIGN_COMMAND`, when set, becomes Tauri's `bundle.windows.signCommand`. Adopting signing is configuration plus a revision of this spec (D74).

## 9. Later list

These are out of v1. They are not part of the phases above.

- Mac and Linux installers, and the third-party notices a Linux bundle would need.
- A per-project asset scope, through a custom URI scheme, so that the webview reads only the open project's files (D27).
- ManimGL and an interactive 3D camera.
- The OpenGL renderer, and parenting any Manim preview window into the webview.
- Freeform Python editing inside the app, including write-back from View code.
- Plugins, a theme or template marketplace, and SVG import.
- Voiceover, physics, and audio tracks.
- Collaboration, accounts, cloud render, and AI scene generation.
- manim-slides presenter mode.
- Tectonic.
- Intra-beat rerender, and any cache finer than one `play` or `wait`.
- Scenes longer than about 2 minutes. v1 refuses a total over 120 seconds rather than trying to make long scenes comfortable.
- Code signing. v1 installers are unsigned, and a SmartScreen warning on first launch is a distribution consequence, not a feature. The release pipeline's signing hook is off (section 8).
- Arrow and dot objects, beat reorder as a drag gesture, a user-editable preview profile, 4K, and a seed field in the inspector.

## 10. Risks

| Risk | What will go wrong | When it shows |
| --- | --- | --- |
| Cairo 3D quality and speed | An 8×8 surface can look coarse and still miss the 20 second insert. Raising the default resolution makes that worse. The cap is 24×24. | Phase 4 measurement. Over 30 seconds revises this spec. |
| TeX size and package set | TinyTeX can dominate the installer. A formula outside the preseeded packages fails closed and must not trigger a download. | Phase 5 size report and the offline `reference-60` export. |
| WebView2 | A clean machine needs the WebView2 runtime, or the window does not open. The installer embeds its offline installer, about 127 MB, and runs it when the runtime is missing. The runtime is Microsoft's, not MIT source. | Phase 1 on a machine without it, and the phase 5 installer. |
| Manim beta breaks | 0.22 is a beta line. A new Manim release can change CLI flags, cache keys, or `MathTex`. v1 stays on `manim==0.22.0`. | Any phase that upgrades the pin. That upgrade is a spec change and a `generatorVersion` bump. |
| Cache invalidation is coarse | Editing an object that enters at beat 0 rerenders the whole scene. Users will expect the tail alone to update. The timeline has to show the dirty range so the wait is explained. | Phase 2, on `lesson-60` and on an axes edit in `reference-60`. |
| Typst and Computer Modern differ | The editor glyph will not match the video. Teachers may report the preview as a bug. The caption and the subset refusal are the mitigation. Rejecting the split in section 7 means revising this spec before phase 3. | Phase 3, side by side on `y = \sin(x)`. |
| Python runtime size is unknown | The "about 1 GB" figure is a bound for planning, not a measurement. PyAV, Cairo, and Manim wheels may land higher, and the precompiled bytecode adds to them. | Phase 5 report. |
| Single render queue | A preview and an export cannot run together. Typing during an export does not update the preview until the export finishes or is cancelled. Long Cairo 3D exports block the queue. | Phase 2 for preview superseding, phase 5 for export blocking. |
| Licensing review | The installer ships GPL and LGPL libraries: x264, x265 and LAME inside PyAV's FFmpeg, and others. The generated notices, the written source offer, the WebView2 entry and the Notices panel's summary have had no legal review. | Before the first public release, which waits for that review (D75). |
| Open release items | The source offer and SECURITY.md name no project address yet. Some components ship no license text, and the worker wheel carries no `LICENSE`. A release tag must be exactly `v<version>`, so there is no release-candidate scheme. The setuptools that builds `srt` is pinned by version, not by hash. The SECURITY.md policy is not yet confirmed. | Before the first public release. |
| Administrator rights | The installer installs for all users under Program Files (D62). Some school PCs do not grant the rights it needs, and there is no per-user install. | Phase 5, and the first installs at a school. |
| Session-wide asset scope | The webview can read every project folder bound since the app started, not only the open one (D27). | Until the per-project scope in section 9. |
| Open project folders are held | On Windows an open project's folder cannot be renamed, moved or deleted in Explorer (section 4, D57 as amended). Users may take that for a bug. | Any phase, whenever a project is open. |
| Interim `beat_ready` | While Manim runs, a `beat_ready` can name the wrong clip when Manim reuses the partial of a beat the index does not count as fresh. The segment list corrects the index when Manim exits, and the UI's `get_state` after `done` shows it. | Previews that reuse older partials. |
| A flush on every save | Every save flushes `project.json` to disk before it replaces the old file. On a slow disk that can push undo past 50 ms; dropping the flush would trade safety for speed. | Phase 2 budget measurements. |

If phase 1 cannot host the sidecar in Tauri, stop. Electron 45 is the fallback named in section 1, and adopting it revises this spec. If a preset cannot be encoded with PyAV, stop and record the failing format. Adding a separate FFmpeg program revises this spec; v1 does not ship one.
