# Phase 2 report — document, codegen, timeline, scrub

Date: 21 October 2026.

Phase 2 only. Phase 3 was not started. No pull request was opened.

These numbers are from a Linux VM. They are not a budget result. The reference machine in section 2 is Windows 11, 8 cores, 16 GB RAM, SSD, integrated graphics, plugged in, High performance. This machine is none of those things, so the Windows timing budgets were not judged here. The spec was not revised.

The one same-machine check that was in scope is the preview regression against phase 1. Phase 1's preview of `reference-60` on this VM was 3.180 s (cold `videos/` partials, warm TeX, preview profile). The cap is 1.5× that time, 4.770 s, and also 67.5 s. This run is under both.

## Machine

Same VM as phase 1: Intel Xeon, 4 cores, 15 GiB RAM, Ubuntu 24.04, Linux 6.12.94+. Not the Windows reference machine.

## Versions

Tauri crate 2.12.5, Tauri CLI 2.12.4, React 19.3.1, Vite 8.3.5, TypeScript 5.9.3, Rust 1.99.1, Python 3.14.8, and Manim 0.22.0 are unchanged.

New: `tauri-plugin-dialog` 2.8.3, used for the native Open and Export code dialogs, with the `gtk3` feature.

This VM also had `gstreamer1.0-libav` installed so WebKitGTK could decode the H.264 preview. That package is not part of Mathimator. Windows WebView2 is the reference player.

## What phase 2 contains

- `project.json` read and write, `schemaVersion` 1 only. A `start` field, another schema version, or an unknown key is refused with a message that names the problem. Saving twice yields the same bytes: two-space JSON, stable key order, uppercase `#RRGGBB`, trailing newline.
- Document model, validation, and derived beat starts. Minimum beat 0.1 s. Maximum total 120 s. A committing edit that breaks either leaves the document unchanged.
- Deterministic codegen, `generatorVersion` 1. Byte-identical to the Journey A block in section 5 and to `scenes/reference_60.py`.
- Timeline and inspector. Top bar: project name, Open, Add, Undo, Redo, Export, View code. Center: preview, play, playhead. Right: inspector. Bottom: beats in order with derived starts and durations.
- Edits: beat duration, color, position shift, formula string (`MathTex`), plot expression, text, anchor. Display-name edits do not change generated Python and do not dirty the preview.
- Objects `axes`, `function_plot`, `math_formula`, `text`. Actions `create`, `write`, `wait`, `set_color`, `move`, plus `fade_in` and `transform_formula` in the model and codegen. No UI inserts `fade_in`.
- Template `axes_function` only. Defaults from section 4: `sin(x)`, Journey A ranges, labels on, color `#58C4DD`, durations 2 s and 2 s. Add also offers Formula and Text. `formula_transform` and `three_d_axes` are not in the menu.
- Expression allowlist. `np.sin(x)`, `sin(x, 1)`, `y` in a 2D plot, and `__import__("os")` are refused with "This expression is not supported." and do not start Manim.
- Partial-movie index, scrub, dirty suffix, last-good frames, badge "Preview is out of date".
- Undo and redo, 100 document edits kept, discarded on exit. Undo history is not in `project.json`.
- View code and Export code use the generator text. The view stays closed until asked. The file has no resolution and no fps.
- `tests/fixtures/lesson-60/project.json` for the invalidation tests.
- The supervisor takes `--project`. The scene is `<project>/.mathimator/codegen/scene.py`. Media is `<project>/.mathimator/cache/media`. Preview and export share it. Export copies the finished MP4 into `.mathimator/exports/<exportKey>/` and writes `export.json`. A preview file is not copied there.
- The asset scope in `tauri.conf.json` is empty. The open project directory is allowed at runtime. `/workspace/out/**` is gone.

Absent, as specified: Typst, equation glyph preview, formula-transform template, 3D, GIF, WebM, PNG sequence, installer, TinyTeX bundling, freeform Python editor.

## Codegen

Byte-identical, whitespace included, header comment included.

- Journey A matches the section 5 block.
- `reference-60` matches `scenes/reference_60.py`, including the four later `play` / `wait` lines.
- The first seven partial-movie SHA-256 hashes from the generated preview match the phase 1 handwritten preview. The scene Manim hashed is the same scene.

## Measurements

Cold partials means `videos/` was deleted. TeX under the same media directory was left in place. Preview profile 854×480, 15 fps. None of this is the reference-machine procedure.

| Check | Result |
| --- | --- |
| Preview through codegen | 3.010 s. Phase 1 on this VM was 3.180 s. 1.5× cap is 4.770 s. 67.5 s cap also holds. |
| Preview file | 854×480, 60.0 s. Eight partials, indexes 0 through 7. |
| Export | 14.178 s. 1920×1080, 60.0 s. Path `.mathimator/exports/969ccfe211d12073/scene.mp4`. SHA-256 differs from the preview file. |
| Partial re-render | 1.433 s from the color edit, including the 300 ms debounce and the edit round trip, until beat index 7's new partial was reported. First 7 partial hashes unchanged. Setup, untimed: the last beat was shortened to 5 s and rendered fresh first. |
| Axes and function insert | 1.187 s from the insert, including the 300 ms debounce, until both default partials existed. The 8 s figure is a reference-machine budget and was not judged. |
| Debug-binary start to `shell-ready.txt` | 0.752 s, on launches that followed another launch. Not an 8 s budget result. |

Journey A in the window, under Xvfb display `:99`:

- Empty project, code view closed, timeline says there are no beats.
- Add → Axes and function. Timeline shows two 2.0 s creates. Inspector shows `sin(x)` and `#58C4DD`. The preview command is 303 ms after the edit. Argv is Cairo, seed 1, 854×480, 15 fps, no `--preview`.
- Add → Formula after the selected beat. LaTeX `y = \sin(x)`, anchor `edge_up`. Renaming the project to "Sine introduction" does not start Manim.
- Scrub moves the playhead label (observed at 2.4 s / 6.0 s). The worker log gains no `argv:` line.
- Export. The button and the status both read "Rendering export" in a screenshot taken about 150 ms after the click. The file is `.mathimator/exports/2b82685e61af5ab7/scene.mp4`, 1920×1080, 6.0 s, not the preview file.
- View code stayed closed. The worker log has no `code` command.
- Undo once restored the name to "Untitled" and did not start Manim.

After `gstreamer1.0-libav` was installed, a scrubbed preview frame showed white axis pixels and cyan curve pixels in the window screenshot. Before that decoder was present, the media element reported error 4.

## Latency

Select, undo, and scrub were not timed as 50 ms or 100 ms budget checks. A scripted click can move the playhead and leave the worker log quiet. That is not a 100 ms frame measurement. The pytest scrub test checks that the scrub command does not spawn Manim. It is labeled as that, not as a latency result.

## Tests

```bash
.venv/bin/python -m pytest tests -q --tb=short
```

29 passed in 50.79 s. That includes the phase 1 render, cancel, and export tests, the phase 2 document and codegen tests, and the phase 2 preview, export, partial re-render, and template-insert renders.

Phase 1 coverage still holds, with the supervisor invoked on a project directory: 8 partials in order, first 7 hashes stable when the last beat changes, `RuntimeError` then `status`, cancel within 1 s, and the section 5 argv.

## Divergences

1. `sceneHash` and the beat cache key omit display names. The spec hashes the scene, and the scene stores names. Names are not used in codegen, and a rename must not dirty the preview. Leaving them in `sceneHash` would also change `exportKey` and throw away a finished export of the same movie.
2. On `lesson-60`, editing `eq_2` changes the `MathTex` line. The `TransformMatchingTex(eq_1, eq_2)` call text stays the same, because that call does not contain the LaTeX. The source before `Write(eq_1)` stays byte-identical. Dirty beats are indexes 6 and 7. Editing `eq_1` dirties indexes 2 through 7. Editing the axes range dirties index 0 onward.
3. `generatorVersion` other than 1 is refused. This phase only emits generator 1.
4. `three_d_scene`, 3D object types, and the `formula_transform` and `three_d_axes` templates are refused as not available in this phase. They are known names, not unknown keys.
5. WebKitGTK on this VM does not play an `asset://` URL in a media element (error 4). The canvas fetches that URL and plays a `blob:` created from the bytes. The shell still passes a file path. The bytes are not base64 on the stdio stream. A percentage height also gave the video element no box; it is now absolutely positioned in the canvas. Windows WebView2 was not tested.
6. `render_frame` was not added. Scrub plays the preview MP4, or the beat partial / last-good file, and does not call `construct`.
7. Tauri's asset scope cannot drop an allow without forbidding the path permanently. Directories opened earlier in the same process stay readable. The configured scope is empty, and this VM's `/workspace/out` path is not in it.
8. Handwritten phase 1 scenes still render when a project directory has `scene.py` and no `project.json`. That is how the phase 1 tests keep the `RuntimeError` scene and the last-beat swap.

## Failures

None in the pytest command above. The preview canvas did not paint frames until the video box and the H.264 decoder were fixed, as described above. Those fixes are in this phase. The Windows budgets were not failed and were not passed.
