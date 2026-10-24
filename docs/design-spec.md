# Mathimator v1 design spec

Status: for approval. Document date: 21 October 2026.

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
| Worker | A Python sidecar supervises renders. Each render is a new grandchild process. On Windows the supervisor assigns that Manim process to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, using `ctypes`. Cancel terminates the job, which also reaps `latex` and `dvisvgm`. The shell assigns the supervisor to its own kill-on-close job so quitting the app reaps the tree. Linux and macOS keep the process-group path through Python's `subprocess` module. The worker does not use `pywin32`, `win32api`, or `taskkill`. |
| Fallback shell | If phase 1 cannot open a Tauri window and host the sidecar, stop. Electron 45 is the documented alternative, and using it is a revision of this spec. |
| Platform | Windows 11 x64 only. The app is a desktop program. It is not a hosted website and not a PySide6 app. |
| Bundling | CI builds a uv virtual environment with CPython 3.14.9, or CPython 3.13.17 if a required wheel is missing for 3.14. One interpreter for the whole environment. The installer ships that environment, a preseeded TinyTeX (`latex` and `dvisvgm`, network installs disabled), and the Typst 0.15.0 binary. v1 does not ship an FFmpeg binary. Manim 0.19 and later encodes with PyAV. An LGPL FFmpeg binary is added only if a test shows PyAV cannot produce a required preset, and that addition is a spec revision. |
| Installer | Offline. When the installer itself must run offline, it embeds the Tauri WebView2 offline installer, about 127 MB. A credible installer size before tuning is several hundred MB up to about 1 GB, with the WebView2 payload in addition when it is embedded. The Python environment size is not yet measured. |
| First user | A teacher or a YouTube explainer. One scene of about 30–90 seconds. |
| Document | Objects and beats, compiled to a Manim scene. Export `.py`. No freeform Python editor. |
| License | MIT for Mathimator's own source, matching Manim Community. Approving this spec confirms that license. The `LICENSE` file is not part of this spec change; it is added when implementation starts. The app is not GPL or LGPL. |

The `manim[typst]` extra is installed because of the pin above. Scene code does not use the `Typst` or `TypstMath` mobjects that extra provides. Those mobjects render Typst to SVG and will not match Computer Modern or arbitrary LaTeX packages. The equation editor calls the Typst 0.15.0 binary for a live glyph. The scene and the export use `MathTex` and classic LaTeX.

Bundled programs keep their own licenses and run as separate programs: CPython, Manim and its dependencies, TinyTeX, Typst, and the WebView2 runtime. TinyTeX contains TeX programs under their own licenses. WebView2 is Microsoft software, not part of Mathimator's source. The Windows shell uses it. The phase 5 installer includes a third-party notices view. Approving MIT approves this split. It is not a legal opinion.

Other Manim front ends exist (manim-slides, manim-editor, manim-studio, JAnim). v1 does not use them as a base or as a library.

### Boundaries

Section 9 is the full out-of-scope list. These boundaries define v1:

- One Windows installer. No Mac or Linux installer.
- Cairo only. No ManimGL, no OpenGL renderer, no interactive 3D camera, no OpenGL window parented into the webview.
- No freeform Python editor, plugins, accounts, cloud render, or AI scene generation.
- No separate FFmpeg binary, and no Tectonic.
- No scene longer than 120 seconds.
- No intra-beat rerender. A changed beat rerenders that whole `play` or `wait`.

### What the window contains

- Top bar: project name, Add, Undo, Redo, Export, View code.
- Center: the preview, a play control, and the playhead.
- Right: the inspector for the selected object, template, or beat.
- Bottom: the beats in order, with derived start times and durations.

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
| Launch to an empty project | 8 s | Window accepts a click, and the worker answers a status ping. | Second process start within a minute of a previous start, so a first-time security scan is not the measurement. No save dialog. The empty project is interactive and the worker is ready within 8 s. |
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

1. Launch the app. Create a project named "Sine introduction".
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

1. Create an empty project.
2. Add → 3D axes.
3. Leave the default surface `sin(x)*cos(y)`, or switch the control to the parametric curve. Set phi, theta, and zoom.
4. Play the preview in the canvas.
5. Export a transparent WebM, or a transparent PNG sequence.

The user sees number fields for a fixed camera. There is no orbit drag and no second window. The surface uses resolution 8×8 unless the user changes it. The preview is the Cairo movie inside the Mathimator window. For WebM and PNG sequence, the transparency control is enabled. The surface fill stays opaque; transparency is the background.

Underneath, the app sets the scene kind to `three_d_scene`, generates the Cairo `ThreeDScene` in section 5, and passes `--renderer cairo`. Camera orientation is a single `set_camera_orientation` call before the beats. The app does not call `begin_ambient_camera_rotation` and does not pass `--live-preview` or `--renderer opengl`. Transparent WebM uses `--format webm --transparent`. Transparent PNG sequence uses `--format png-sequence --transparent`. `--format png` is the wrong flag: in Manim 0.22 it writes the last frame only.

Cairo limits, stated in the inspector for this template: software rendering, flat-shaded faces, a static camera, and a resolution cap of 24×24. The 8 second budget does not apply. The 20 second budget does. A 3D template is refused unless the project has no objects and no beats ("A 3D template needs an empty project."). A 2D template is refused in a 3D project ("This template is for a 2D scene."). A formula or text object is refused there too ("A 3D scene only holds the 3D template."). A 3D project contains the template's axes and one surface or curve.

Failure: any window other than Mathimator opens; the command line contains `opengl` or `--live-preview`; the export is 854×480; transparency is offered for MP4; or the axes and surface are not both visible within 20 seconds. A 20 second miss over 30 seconds revises this spec before the next phase.

## 4. Scene data model

A project is a folder. `project.json` is the document. Everything under `.mathimator/` is derived.

```text
<project>/
  project.json
  .mathimator/
    codegen/scene.py
    cache/media/
    cache/last-good/
    exports/<exportKey>/
    typst/
    logs/worker.log
```

Deleting `.mathimator/cache`, `codegen`, `typst`, or `logs` is safe. Clear cache does that and leaves `project.json` and `.mathimator/exports/` in place. v1 does not call `manim cache clear`, and it does not pass `--flush_cache` (removed in 0.22). The cache v1 owns is the project cache directory.

On save, `project.json` is pretty-printed with two-space indentation, a trailing newline, uppercase `#RRGGBB` colors, and stable key order. The same document saved twice has the same bytes. Hashing uses a separate canonical form: UTF-8, sorted keys, no insignificant whitespace, array order preserved, numbers with three decimal places.

### Project

| Field | Rule |
| --- | --- |
| `schemaVersion` | Integer. v1 reads and writes `1` only. Any other value, or a missing value, is refused. The message names the file's version. |
| `generatorVersion` | Integer `1` for code that matches section 5. Bump it when generated Python changes. It is part of the cache key. |
| `id` | Stable id matching `[a-z][a-z0-9_]{0,31}`. |
| `name` | Display name. Not used in codegen. Renaming it does not dirty a preview. |
| `seed` | Integer. v1 always writes `1` and shows no seed control. |
| `scene` | The only scene. |
| `previewProfile` | Fixed: width 854, height 480, fps 15, `transparent` false. |
| `exportProfile` | `format` (`mp4`, `gif`, `webm`, or `png_sequence`), `width`, `height`, `fps`, `transparent`. |

There is no scene list. There is no `start` field anywhere. A file that contains `start`, an unknown key, or an unknown object type is refused.

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

Object fields: `id`, `name`, `type`, `params`. Beat fields: `id`, `duration`, `action`, `targets`, and `params` when the action needs them. Ids are unique across the scene, objects, beats, and template instances. The playhead, selection, dirty flags, and undo stack are not stored. Undo history holds at least 50 document edits per session and is discarded on exit.

Derived start of beat `i` is the sum of durations of beats `0 .. i-1`. The timeline displays it and does not accept an edit to it. Total duration is the sum of beat durations. Minimum beat duration is 0.1 seconds. Maximum total is 120 seconds. A committing edit that breaks either limit is refused, and the document is unchanged.

### Object types

| Type | Used in | Params |
| --- | --- | --- |
| `axes` | 2D | `xRange`, `yRange` as `[min, max, step]`, `xLength`, `yLength`, `color`, `showLabels` |
| `function_plot` | 2D | `axesId`, `expression`, `xMin`, `xMax`, `color` |
| `math_formula` | 2D | `latex` (no delimiters, at most 500 characters), `color`, `anchor`, `shift` |
| `text` | 2D | `text` (at most 200 characters), `color`, `anchor`, `shift` |
| `three_d_axes` | 3D | `xRange`, `yRange`, `zRange` as `[min, max, step]`, lengths optional and defaulted by the template |
| `surface` | 3D | `axesId`, `expression` in `x` and `y`, `uRange`, `vRange`, `resolution` as `[u, v]`, `color` |
| `parametric_curve` | 3D | `axesId`, `components` as three expressions in `t`, `tRange` as `[min, max]`, `color` |

`anchor` is `origin`, `edge_up`, `edge_down`, `edge_left`, or `edge_right`. `shift` is `[dx, dy]` in Manim units, y up. Omitted `shift` means `[0, 0]`.

v1 has no arrow and no dot. The journeys do not need them, and there is no general mobject factory.

Expressions use a fixed grammar. `expr` is terms joined by `+` or `-`. A term is factors joined by `*` or `/`. A factor is a primary, optionally followed by `^` and one primary. A primary is a number, the variable, `pi`, a call of `sin`, `cos`, `tan`, `exp`, `log`, or `sqrt` with one argument, parentheses, or a unary minus. The 2D variable is `x`. A surface uses `x` and `y`. A curve uses `t`. `log` is the natural log, and the inspector says so. `^` binds tighter than `*` and `/`, which bind tighter than `+` and `-`.

Rejected examples, with no render started: `np.sin(x)`, `sin(x, 1)`, `y` in a 2D plot, `__import__("os")`. The field shows "This expression is not supported."

### Actions

| Action | Targets | Role |
| --- | --- | --- |
| `create` | one | Entrance for axes, function plots, 3D axes, surfaces, curves. |
| `fade_in` | one | Entrance allowed for every object type. |
| `write` | one | Entrance for `math_formula` and `text` only. |
| `wait` | none | Hold. |
| `transform_formula` | source, destination | One `play`. Destination must be a `math_formula` whose entrance is this beat. Source is a `math_formula` that has already entered. |
| `set_color` | one already on screen | `params.color`. |
| `move` | one already on screen | `params.shift` as `[dx, dy]`. |

Every object has exactly one entrance beat. A beat may mention an object only at its entrance or after it. `transform_formula` is the destination's entrance, so a transformed formula has no separate `create`.

### Templates

A template instance is a recorded insertion, not a plugin. Its fields are `id`, `templateId`, `params`, `objectIds`, and `beatIds`. Params are the source of truth for the objects it created. Editing those params rewrites those objects in place and keeps ids, beat ids, beat actions, and beat durations. On open, linked objects are rewritten from the instance so a hand edit to a generated object param does not survive. User beats that sit between template beats are left in place. Switching the 3D template between surface and curve replaces the second object, retargets the second beat, and keeps both beat ids. There is no template directory and no third-party loader.

| Template id | Inserts | Defaults |
| --- | --- | --- |
| `axes_function` | `axes`, `function_plot`, two `create` beats | `sin(x)`, ranges as in the Journey A JSON, labels on, color `#58C4DD`, durations 2 s and 2 s |
| `formula_transform` | two formulas, a `write` beat, a `transform_formula` beat | `y = \sin(x)` to `y = \cos(x)`, anchor `edge_up`, durations 2 s and 2 s |
| `three_d_axes` | `three_d_axes`, one `surface` or `parametric_curve`, two `create` beats, camera | Axes ranges `[-3, 3, 1]`, `[-3, 3, 1]`, `[-2, 2, 1]`. Surface `sin(x)*cos(y)`, `u`/`v` range `[-2.5, 2.5]`, resolution `[8, 8]` with a maximum of `[24, 24]`, phi 65, theta −45, zoom 1, durations 2 s and 2 s. Curve mode uses components `cos(t)`, `sin(t)`, `t / 3` and `tRange` `[0, 12.56]`. |

New beats and template insertions are placed after the selected beat, or appended when nothing is selected. Deleting an object also deletes every beat that targets it, as one undo step, and dirties from the first removed index.

### Cache identity

A beat's cache key is the SHA-256 of Manim version `0.22.0`, `generatorVersion`, the active profile (preview or export), the beat index, the beat's canonical JSON, the canonical JSON of the objects that beat mentions (a function plot includes its axes; a surface or curve includes its axes), and the seed.

`sceneHash` is the hash of `schemaVersion`, `generatorVersion`, `seed`, and `scene`. `exportKey` is `sceneHash` plus the export profile plus the Manim version. The finished export folder is named with the first 16 hex characters of `exportKey`. A manifest beside the file stores the full hash, and a mismatch is not the current export. The Manim media directory is not named with these hashes. Putting the scene hash in the media path would discard partial-movie reuse. Encoder settings stay at Manim's defaults; v1 does not pass `--video-codec` or `--encoder-option`. Changing those defaults in a future Manim pin requires a new `generatorVersion`.

Dirty flags are derived from these keys. They are not stored in `project.json`.

### Journey A document

This is the normative `project.json` for Journey A.

```json
{
  "schemaVersion": 1,
  "generatorVersion": 1,
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

The generated file defines `MainScene` and does not instantiate it. Quality, format, and transparency are command-line arguments, so the same file is the preview and the export. The worker runs it with the bundled interpreter:

```text
<python> -m manim render scene.py MainScene --renderer cairo --seed 1 -r 854,480 --fps 15 --format mp4 --media_dir <project>/.mathimator/cache/media --max-inflight-encoders 1 --progress_bar none
```

Preview and export use that same `--media_dir`. The export command changes `-r`, `--fps`, and `--format`, and adds `--transparent` only when that format allows it. Manim separates quality variants in folders under the media directory, so a 854×480 partial is not a 1920×1080 partial. `--max-inflight-encoders 1` keeps Manim's default: each partial movie is finished before the next beat starts. The worker never passes `--preview`, `--live-preview`, `--renderer opengl`, `--disable_caching`, `--flush_cache`, or `--from_animation_number`.

`-n` / `--from_animation_number` is an animation index, not a timestamp. v1 always renders the whole scene and lets Manim reuse earlier partial movies. Seeking does not use that flag.

`Scene.update_to_time` only advances the animation already inside `play_internal`. Cairo `Camera.get_image()` returns the frame the camera is holding. Neither call seeks to an arbitrary time. The scrub path does not call them, and it does not call `construct`.

### Journey A, normative output

```python
# Generated by Mathimator. generatorVersion 1. manim==0.22.0
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

Each object is constructed immediately before its entrance `play`. Labels belong to the axes entrance, so they are inside that one `play`, not a second beat. `run_time` is the beat duration. Whole numbers are emitted as integers; other numbers use up to three decimal places with trailing zeros removed.

| Action | Emitted call |
| --- | --- |
| `create` | `self.play(Create(target), run_time=duration)` |
| `fade_in` | `self.play(FadeIn(target), run_time=duration)` |
| `write` | `self.play(Write(target), run_time=duration)` |
| `wait` | `self.wait(duration)` |
| `transform_formula` | `self.play(TransformMatchingTex(source, dest), run_time=duration)` |
| `set_color` | `self.play(target.animate.set_color("#RRGGBB"), run_time=duration)` |
| `move` | `self.play(target.animate.shift(np.array([dx, dy, 0.0])), run_time=duration)` |

Anchors: `origin` is `move_to(ORIGIN)`; `edge_up`, `edge_down`, `edge_left`, and `edge_right` are `to_edge` of `UP`, `DOWN`, `LEFT`, and `RIGHT`. A non-zero shift is a `shift(np.array([dx, dy, 0.0]))` after the anchor.

Expression examples the generator must emit:

| Expression | Lambda body |
| --- | --- |
| `sin(x)` | `np.sin(x)` |
| `sin(x)^2` | `np.sin(x) ** 2` |
| `2*x + 1` | `2 * x + 1` |

`MathTex` receives the stored LaTeX as a raw string. The translator in section 6 is not used here. Generated scene source contains `MathTex` for every formula and does not contain `TypstMath`, `MathTypst`, or `manim.typst`.

`reference-60` uses this same object code. Its `play` / `wait` lines use the section 2 table, including:

```python
self.play(plot_1.animate.set_color("#E07A5F"), run_time=8)
self.play(title_1.animate.shift(np.array([0.0, -0.4, 0.0])), run_time=8)
self.wait(8)
self.play(title_1.animate.set_color("#F2CC8F"), run_time=8)
```

### Invalidation

Editing beat `i` marks beats `i..n` dirty and leaves `0..i-1` cached. The generated source for the unaffected prefix stays byte-identical, so Manim's per-`play` hashes still match.

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

The shell starts the supervisor and the Typst processes. The supervisor starts Manim.

The **supervisor** is a long-lived Python sidecar on stdio. It does not import Manim or PyAV. It reads `project.json`, writes `scene.py`, spawns the Manim process, watches the partial-movie directory, and forwards events. Before each render it deletes and recreates `.mathimator/codegen/`, so the folder Manim imports `scene.py` from holds nothing else: a shared project folder cannot plant bytecode or a module there. `render_frame` is a separate short-lived helper so PyAV stays out of the supervisor. If the Manim process crashes, including a native Cairo crash, the supervisor emits an error and stays alive. The UI shows the error and keeps the last good frames. The shell restarts a dead supervisor and does not silently retry the failed job.

The **Manim process** is `<python> -m manim render ...` as specified in section 5. Manim's config is process-global, so every render is a new interpreter. One Manim process at a time.

**Typst** is started by the shell, not by the supervisor and not by Manim. A new keystroke kills the previous Typst process. Typst is never queued behind a scene render.

Frames and movies cross the shell as file paths. They are not base64 on the stdio stream. The webview may read files inside the open project directory and nowhere else.

### Protocol

Stdio carries one UTF-8 JSON object per line.

| Command | Meaning |
| --- | --- |
| `status` | Supervisor is ready. The cold-start test waits for this. |
| `render_preview` | Render the current document at the preview profile. |
| `render_frame` | Write one PNG for a time, using PyAV on an existing partial movie. Do not import the scene and do not call `construct`. |
| `export` | Render at the export profile. |
| `cancel` | Kill the grandchild process tree for that job id. On Windows, terminate its Job Object. |

Events are `ready`, `beat_ready` (beat index and partial-movie path), `done` (output path), and `error`. The UI ignores events whose job id is not the current job.

Dirty beats are a suffix of the timeline. Before a job, the supervisor copies those beats' current partials into `cache/last-good/` and snapshots `partial_movie_files/MainScene` for this job's quality folder. Manim finishes one beat before the next. Each new or replaced partial, in completion order, belongs to the next dirty beat. The supervisor writes a preview index: beat id, derived start, duration, partial path, and whether that beat is fresh. `render_frame` reads that index from a short-lived helper process, so the supervisor does not import PyAV. Scrub does not call `render_frame`. Phase 1 checks that a full render yields 8 partials in beat order, and that changing the last beat leaves the first 7 hashes unchanged.

Manim reuses any partial movie that exists, so a cut-off partial would break every later render of its beat. The supervisor records a partial in the preview index, or marks its beat fresh, only when its container is complete. An MP4 or MOV is complete when its top-level boxes add up to exactly the file length, none has size 0 (the placeholder FFmpeg fills in when it closes a file), and one of them is `moov`. A WebM is complete when an EBML header is followed by top-level elements of known size that add up to exactly the file length, one of them a Segment. The check uses the standard library only. Before each render the supervisor deletes the incomplete partials in this job's quality folder, so those beats render again. A render that exits 0 after using an incomplete partial is an error: that partial is deleted, in whichever quality folder it lies, nothing is published, and the next render redoes its beat. A PNG-sequence export writes no partials, so it reads no segment list.

Before it starts Manim, the supervisor writes `.mathimator/cache/manim-run.json` with the start time, and it removes the file when the run ends, whatever the outcome. A run that is cancelled or exits non-zero deletes the media it wrote, except the complete partials in its quality folder and a movie Manim reported ready. A quit, a crash or a kill can leave the marker behind. Whenever a folder is bound (startup, Open) while the marker exists and no render of that folder is running, the supervisor deletes the media written since that start time, then the marker. A marker it cannot read counts from its own modification time.

### Profiles

The preview profile is fixed at 854×480, 15 fps, opaque, on the scene background color. It is not a user control in v1. The canvas does not fake a transparent checkerboard.

Export formats and the allowed resolutions (854×480, 1280×720, 1920×1080) and frame rates (15, 30, 60) are in section 7. Preview and export share `cache/media`. A preview-quality partial is never copied into `exports/`. The acceptance test probes the exported frame size.

### Scrub and playback

When every beat is fresh, the canvas plays the MP4 Manim wrote for the preview, and scrub sets that element's time. When any beat is dirty, the canvas shows the partial movie for the beat under the playhead: the new file if that beat is fresh, otherwise the last good file, with the badge "Preview is out of date". If a beat has never had a partial movie, the canvas is blank and the badge is shown.

Playback is a timer on those files. It does not call Manim. `cache/last-good/` holds the previous partial for a dirty beat until the replacement partial is ready. Clear cache may delete it.

### Stale preview and export

The Export action stays available. The progress label "Rendering export" is shown within 200 ms, from the UI, before the supervisor answers.

A file is the current export only when an `export.json` manifest beside it records the current `exportKey`. If that file already exists, the supervisor returns it and does not start Manim. Otherwise the supervisor runs an export-quality render. On exit code 0 it copies the primary output into `.mathimator/exports/<exportKey>/` and writes the manifest. Preview files are never copied there.

If the document changes while an export runs, the export continues for the `exportKey` captured at the click. Typing does not cancel it and does not start a new export. When it finishes, the file is kept for the hash it was rendered from. It becomes the current export only if that hash is still current. A second Export click while one is running is ignored, and the UI says an export is already running.

Cancel discards an unfinished export output. A previous finished export for an older hash is left in place and is not labeled current.

### Job queue

- At most one Manim grandchild.
- A new preview for this scene cancels the in-flight preview. Late output from the cancelled job is ignored.
- Document edits never cancel an export and never start one.
- An Export click cancels an in-flight preview, then starts the export.
- While an export runs, the newest preview request waits and runs after the export ends or is cancelled.
- Preview cancel uses the same 1 second process-exit criterion as export cancel.

On Windows the supervisor puts each Manim child in a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, using `ctypes`, and cancel terminates the job. `latex` and `dvisvgm` are gone within 1 second. The Tauri shell assigns the supervisor to a kill-on-close job so quitting the app reaps the same tree. Linux and macOS terminate the process group through `subprocess`. The worker does not use `pywin32`, `win32api`, or `taskkill`. Craig approved this Windows binding; it replaces the earlier rule that forbade every Win32 call.

### Equation preview

The stored formula is LaTeX. After a 100 ms debounce, a translator converts the supported subset to Typst math and the shell runs Typst 0.15.0 `compile` on one `.typ` file to one `.svg`.

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

`tests/fixtures/equation/translate-cases.json` holds the expected Typst math for each test formula, with `null` where there is no preview.

`\int` and any other command produce no SVG. The editor shows "No live preview for this formula." The scene can still render that formula with `MathTex`. A failed Typst run keeps the last good SVG and shows the error. The caption "Live preview is Typst. The video uses LaTeX and will look different." stays visible whenever a formula is selected.

Tectonic is not on the render path. Final LaTeX uses the bundled `latex` and `dvisvgm`. If a formula needs a package that was not preseeded, the render fails with "LaTeX failed." and the TeX log line. It does not download the package.

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
| PNG sequence | 1920×1080, 30 fps | Optional, including the background. One PNG per frame. | `--format png-sequence`, plus `--transparent` when the control is on |

Manim writes every PNG sequence to one folder, `images/<scene>/MainScene`, and never empties it. A PNG-sequence export deletes that folder before Manim starts; if it cannot, the export fails. It publishes the frames only when their number equals the count Manim reports, which guards against stale frames left in the folder.

Resolutions are 854×480, 1280×720, and 1920×1080. Frame rates are 15, 30, and 60. The 3 minute budget applies to `reference-60` at 1920×1080, 30 fps, MP4, cold export cache. Other combinations must finish and match their profile; they do not inherit that time limit.

When transparency is on, a sample background pixel has alpha 0. Object pixels for the default surface stay opaque. The preview stays opaque.

Pass when all of these hold:

- The progress label appears within 200 ms, and cancel meets the 1 second test.
- A 1080p30 MP4 of `reference-60` is within one frame of 60 seconds. Over 270 seconds revises this spec.
- The export file's frame size equals the export profile. A preview-sized file fails.
- MP4 and GIF cannot enable transparency. WebM and PNG sequence can, and the alpha check passes.
- A second export of an unchanged `exportKey` does not start Manim.
- Export code writes the same Python as View code, and that text matches the generator. The file does not contain a resolution or an fps.

### Save, open, undo, and offline launch

Pass when all of these hold:

- A committed edit is flushed to `project.json` before the preview job starts, once the project has a folder. Reopening shows that edit. Undo history is empty after reopen.
- Undo and redo restore document edits within 50 ms. Dirty flags are recomputed and are absent from the file.
- A file with `schemaVersion` other than 1, a `start` field, or an unknown key is refused.
- On Windows, cancel and quit leave no Manim, `latex`, or `dvisvgm` process within 1 second. Each Manim child sits in a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and the supervisor itself is in a kill-on-close job. The binding is `ctypes`. The worker package contains no `pywin32`, `win32api`, or `taskkill`. Linux and macOS use the process-group path.
- After the phase 5 installer exists: on a clean Windows 11 account with no Python, FFmpeg, TeX, or Typst installed, and with the network disconnected, the app installs, launches, opens the bundled `reference-60` sample, and exports a 1080p30 MP4.

The phase 5 report records venv size, TinyTeX size, Typst size, WebView2 size, and installer size. There is no pass/fail size in this spec. The expected toolchain range before tuning is several hundred MB to about 1 GB, plus about 127 MB for the embedded WebView2 offline installer.

### Tests by phase

Automated tests are pytest for the worker, the document, and codegen, plus scripted UI checks for the latency budgets.

| Phase | Tests that must exist |
| --- | --- |
| 1 | Worker renders `reference-60` to 8 partial movies, in beat order, and a preview MP4 within one frame of 60 s. Changing the last beat and rendering again leaves the first 7 partial-movie hashes unchanged. A scene that raises `RuntimeError` returns an error, and a following `status` succeeds. Cancel ends the grandchild and any TeX child within 1 s. The argv contains `--renderer cairo` and `--seed`, and does not contain `opengl`, `--live-preview`, or `--preview`. |
| 2 | Byte-identical codegen for the Journey A block and for `reference-60`. Prefix stability: editing the last beat does not change the earlier source. Invalidation on `lesson-60` as in section 5. Derived times, refusal of a stored `start`, save/open, undo/redo, display-name stability, scrub with a quiet worker log, expression rejection of `__import__("os")`. |
| 3 | Translator fixtures for every supported command and for `\int`. Equation budget on `y = \sin(x)`. Generated source uses `MathTex` only. An unsupported formula still renders in the scene. |
| 4 | All three templates insert the specified objects and beats. 3D source and argv match section 5 for surface and curve. Refusal rules for mixed 2D/3D projects. 20 second insert measurement. |
| 5 | Export matrix in section 7, including alpha checks and the ban on upscaling. `typst --version` is 0.15.0. With the network disconnected, the bundled interpreter imports `manim`, and `latex` and `dvisvgm` run. The clean-machine install test. Notices mention Manim, Typst, and TinyTeX. |

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

In: the export preset UI; resolution, fps, and transparency rules; third-party notices; a uv environment pinned as in section 1; preseeded TinyTeX with network installs disabled; the Typst 0.15.0 binary; an offline Tauri Windows installer that embeds the WebView2 offline installer; the `reference-60` project bundled as a sample. The installer is unsigned. A SmartScreen warning on first launch is expected.

Absent: everything in section 9. In particular, no Mac or Linux installer, no FFmpeg binary unless a preset test has already failed and this spec has been revised, and no code signing.

TinyTeX is preseeded with the packages imported by Manim 0.22's default `MathTex` template, including what that template needs for `amsmath`, `amssymb`, and `dvisvgm`. The offline render is the proof. A missing package is added to the seed list before any other phase 5 work continues. If `manim[typst]==0.22.0` has no wheel for 3.14.9, the environment uses 3.13.17. If it has no wheel for either, stop. Do not change the Manim pin.

Tests: the phase 5 row, plus the full section 2 budget suite on the reference machine against the installed app. The clean-machine VM proves offline install and a finished export. It does not replace the reference machine for the time budgets unless it matches the reference profile.

Sample: `lesson-60` exported as a GIF, and `reference-60` exported as a 1080p30 MP4 from the installed app on the clean machine.

Gate: launch the app, run this phase's tests, render the sample scene, export the sample scene, and stop for approval.

## 9. Later list

These are out of v1. They are not part of the phases above.

- Mac and Linux installers.
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
- Code signing as a product feature. The phase 5 installer is unsigned; a SmartScreen warning is a distribution consequence, not a feature.
- Arrow and dot objects, beat reorder as a drag gesture, a user-editable preview profile, 4K, and a seed field in the inspector.

## 10. Risks

| Risk | What will go wrong | When it shows |
| --- | --- | --- |
| Cairo 3D quality and speed | An 8×8 surface can look coarse and still miss the 20 second insert. Raising the default resolution makes that worse. The cap is 24×24. | Phase 4 measurement. Over 30 seconds revises this spec. |
| TeX size and package set | TinyTeX can dominate the installer. A formula outside the preseeded packages fails closed and must not trigger a download. | Phase 5 size report and the offline `reference-60` export. |
| WebView2 | A clean machine needs the offline runtime, about 127 MB, or the window does not open. The runtime is Microsoft's, not MIT source. | Phase 1 on a machine without it, and the phase 5 installer. |
| Manim beta breaks | 0.22 is a beta line. A new Manim release can change CLI flags, cache keys, or `MathTex`. v1 stays on `manim==0.22.0`. | Any phase that upgrades the pin. That upgrade is a spec change and a `generatorVersion` bump. |
| Cache invalidation is coarse | Editing an object that enters at beat 0 rerenders the whole scene. Users will expect the tail alone to update. The timeline has to show the dirty range so the wait is explained. | Phase 2, on `lesson-60` and on an axes edit in `reference-60`. |
| Typst and Computer Modern differ | The editor glyph will not match the video. Teachers may report the preview as a bug. The caption and the subset refusal are the mitigation. Rejecting the split in section 7 means revising this spec before phase 3. | Phase 3, side by side on `y = \sin(x)`. |
| Virtual environment size is unknown | The "about 1 GB" figure is a bound for planning, not a measurement. PyAV, Cairo, and Manim wheels may land higher. | Phase 5 report. |
| Single render queue | A preview and an export cannot run together. Typing during an export does not update the preview until the export finishes or is cancelled. Long Cairo 3D exports block the queue. | Phase 2 for preview superseding, phase 5 for export blocking. |

If phase 1 cannot host the sidecar in Tauri, stop. Electron 45 is the fallback named in section 1, and adopting it revises this spec. If a preset cannot be encoded with PyAV, stop and record the failing format. Adding an LGPL FFmpeg binary revises this spec; v1 does not ship one in advance.
