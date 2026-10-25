# Windows audit report

> Historical record. Where it differs from the current branch, [design-spec.md](design-spec.md) describes the current behaviour, and section 8 of [architecture-audit-report.md](architecture-audit-report.md) the remediation status.

Date: 22 October 2026. Machine: Lenovo 21DC0032US, 12th Gen Intel Core i9-12900H (14 cores / 20 threads), about 64 GB RAM, Windows 11 Pro 10.0.26200 x64. The account is not an administrator. This is not the section 2 reference machine (8 cores, 16 GB, integrated graphics). Timings below are measurements on this PC. They are not budget-gate results.

The network stayed connected. MiKTeX's own configuration was not changed. The GUI run left `MATHIMATOR_WIN32_JOBS` and `MATHIMATOR_WIN32_TESTS` unset and set `MATHIMATOR_SKIP_PROCESS_TABLE=1`, so that launch did not create a Job Object and did not start the kernel32 process-table helper. A later code change makes the Windows job the default. The verification commands below set `MATHIMATOR_WIN32_JOBS=0` and `tests/conftest.py` does the same unless `MATHIMATOR_WIN32_TESTS=1`. Those reap tests were not executed.

## Setup that worked

| Piece | What this PC actually used |
| --- | --- |
| Dev Python | `.venv` CPython 3.14.2 with Manim Community 0.22.0 |
| Bundle Python | `packaging/bundle/venv`, CPython 3.14.9, Manim 0.22.0 from `manim[typst]==0.22.0`, `pythonSelection` "uv published 3.14.9" |
| uv | The script uses cached uv 0.13.5. PATH uv 0.10.9 does not list CPython 3.14.9 and would silently pick 3.14.3. |
| LaTeX for dev tests | MiKTeX-pdfTeX 4.26 (MiKTeX 26.2) at `%LOCALAPPDATA%\Programs\MiKTeX\miktex\bin\x64`. It is not on PATH until prepended. |
| Bundle TeX | TinyTeX 2026.10, `tlmgr` revision 80658, `packaging/bundle/tinytex/bin/windows`. The Windows asset is a 7-Zip SFX (`installer.exe -y -o<dir>`), then the nested `TinyTeX\` directory is flattened. `tlmgr.bat` does not propagate perl's exit code; the script calls `tlpkg\tlperl\bin\perl.exe` on `tlmgr.pl`. |
| Typst | 0.15.0 at `packaging/bundle/typst/typst.exe`. Not on PATH until prepended. Lookup must try `typst.exe` as well as `typst`. |
| Rust / Tauri | rustc 1.93.0, Tauri CLI 2.12.4, crate tauri 2.12.5. Node v24.22.1. |
| WebView2 | Installed (runtime 155.0.4283.62). The phase 5 report measured the embedded offline installer at 214,156,024 bytes. The spec's planning figure is about 127 MB. |
| Installer | `desktop/src-tauri/target/release/bundle/nsis/Mathimator_0.1.0_x64-setup.exe`, 400,153,637 bytes (381.62 MiB). It was not installed or uninstalled. A clean disconnected machine was not available. |

`packaging/build-bundle.ps1` is what `packaging/build-bundle.sh` runs under MSYS. The bundle directory is gitignored. Rebuilding the bundle copies `worker/mathimator_worker` into `packaging/bundle/worker`. The release exe prefers that copy, so a source-tree worker fix is not what the already-built exe runs until the bundle and the Tauri resources are rebuilt.

## Journeys

The release exe was driven with UI Automation. WebView2 buttons do not support InvokePattern, so clicks use the window's bounding rectangle. Screenshots were taken with `System.Drawing.CopyFromScreen`. `out/shot.py` uses `ctypes.windll` and was not run.

| Journey | Result | Evidence |
| --- | --- | --- |
| A launch and name | Pass | Ready in 1.407 s. Project name became "Sine introduction". `01-empty-ready.png`, `02-renamed.png`. |
| A axes and sine | Pass | Add → Axes and function reached Ready in 3.383 s. At 0.0 s the Create animation is still empty, so the canvas is black. At 2.08 s the axes are on screen. The 1080p30 export frame at 3.5 s shows the sine curve, and the frame at 5.5 s shows `y = sin(x)` above it. |
| A formula editor | Pass | Caption "Live preview is Typst. The video uses LaTeX and will look different." The inspector shows the Typst glyph. The field value was `y = \sin(x)`. `04-journey-a-formula.png`. |
| A scrub | Pass | Playhead moved to 2.08 s and the canvas showed the axes. Scrub did not report a new Manim launch in the status line. |
| A 1080p30 MP4 | Pass | "Rendering export" was visible at 91.2 ms (UI Automation poll). The file is h264, 1920×1080, 6.0 s, 30 fps, and it is a different file from the 854×480 preview. "MP4 cannot store transparency." `06-journey-a-export-panel.png`, `frame-journey-a-export-5.5s.png`. |
| B open lesson-60 | Pass for the document | Sample opened the swapped lesson. "Sine equation" was in the tree in 0.159 s. That is the document appearing, not a finished preview frame. The canvas still said "No frame yet." while the first preview was running. |
| B edit both formulas | Pass | `eq_1` became `y = \sin(2x)`, `eq_2` became `y = \cos(2x)`. |
| B scrub the tail | Partial | Playhead 55.89 s. Badge "Preview is out of date" was showing. There was no last good frame yet ("No frame yet."), because this was a cold project. The warm-cache rule (axes partials stay cached) was not what this screenshot shows. |
| B undo | Pass | Undo restored the formula field to `y = \cos(x)`. The worker log's document matches. |
| B GIF | Fail in the GUI, pass headless | The panel showed "GIF cannot store an alpha channel." and the transparency checkbox was disabled (`gif_transparency_enabled` false). Export then failed: Manim `InvalidDataError` while concatenating partials. A headless lesson-60 GIF earlier in this audit wrote an 854×480, 60.0 s GIF in 15.815 s. See the issue below. |
| B notices | Pass | The notices panel lists Manim (MIT), Typst (Apache 2.0), TinyTeX, WebView2, and CPython. `13-3d-refused.png` still shows that panel. |
| B 3D on a non-empty project | Pass | "A 3D template needs an empty project." |
| C empty project, 3D axes | Pass | Insert reached Ready in 5.216 s. Cairo limits text is in the inspector. Default expression `sin(x)*cos(y)`, resolution 8×8, phi 65. No second window. |
| C play | Pass | Play advanced the clock (0.812 s in `15-journey-c-playing.png`, axes appearing). The preview file at 3.0 s shows the axes and the surface. |
| C transparent PNG | Pass | PNG sequence, 1920×1080, 30 fps, transparency checked, note "Transparency includes the background." 120 frames. Frame 0090 is RGBA, corner alpha 0, a surface pixel is opaque `(181, 254, 254, 255)`. |
| C transparent WebM | Control only | The WebM preset enables transparency and shows the same note. The export that was started was the PNG sequence, not WebM. The known WebM alpha failure is below. |

The release exe's Python worker was `runtime/worker`, copied from the bundle before the source fixes in this branch. UI behavior (caption, presets, 3D refusal, undo text) is from that run. Worker-side fixes are covered by pytest against the source tree.

## Section 7

| Criterion | Result on this PC |
| --- | --- |
| reference-60 preview scrubs without a new Manim, within 100 ms | Worker renders produced the preview. A scripted 100 ms scrub was not repeated in the window. UI scrub moved the playhead and the picture. |
| Partial rerender within 3 s, earlier hashes unchanged | 2.581 s, first 7 partial hashes unchanged (`out/phase2/timings.json`). |
| Editing beat i dirties i..n; lesson-60 eq_2 dirties only the tail | Covered by the worker tests. The cold GUI open marked every beat dirty because no preview had finished. |
| "Preview is out of date" while dirty, last good frame kept | The badge appeared. On the cold lesson there was no last good frame. |
| axes_function insert within 8 s | Worker 2.109 s including the 300 ms debounce. UI 3.383 s. Not a reference-machine gate. |
| `y = \sin(x)` shows an SVG inside the equation budget, Manim not used for that SVG | The glyph and caption were visible in the inspector before the scene render finished. |
| `\int_0^1 x dx` shows "No live preview for this formula." and still renders through MathTex | Not repeated in the window this run. The equation-editor tests cover it. |
| Scene source has MathTex and no TypstMath | The lesson log's generated scene uses `MathTex` and does not mention Typst. |
| Caption visible while a formula is selected | Pass. |
| Templates insert the section 4 objects | Axes and function, formula, and 3D axes did. |
| 3D refused unless the project is empty; 2D refused in a 3D project | The empty-project refusal was shown. The 2D-into-3D refusal was not clicked this run. |
| 3D insert within 20 s | Worker 5.966 s. UI 5.216 s. |
| Export progress within 200 ms | First UI Automation observation of "Rendering export": 91.2 ms (MP4), 98.9 ms (GIF), 96.4 ms (PNG). The poll is coarser than an in-page timestamp. |
| reference-60 1080p30 within 60 s, revise over 270 s | 22.018 s in `out/phase5/reference-60-mp4.json` (also 27.416 s in the phase 1 export file). Not a reference-machine gate. |
| Export frame size matches the profile | Journey A export is 1920×1080. Its preview is 854×480. The hashes of the phase 5 export and preview differ. |
| MP4 and GIF cannot enable transparency; WebM and PNG can; alpha check | MP4 and GIF controls matched the spec text. PNG alpha passed (corner 0). WebM alpha failed (corner 255, yuv420p, vp9). |
| Second unchanged export does not start Manim | `second_export_skipped_manim: true` in the phase 5 MP4 record. |
| Cancel and quit reap Manim, latex, and dvisvgm within 1 s via a Job Object | Spec text is updated. The implementation is gated off. The tests are written and were not executed. |
| Clean disconnected install of the offline installer | Not run. The NSIS file exists (381.62 MiB). This account is not a clean machine. |
| Launch within 8 s, open within 3 s | UI Ready in 1.407 s. Lesson objects in the tree in 0.159 s. Not reference-machine gates. |

## Timings against section 2

All figures are this machine, not the reference machine.

| Budget | Limit | Measured |
| --- | --- | --- |
| Launch to Ready | 8 s | 1.407 s (UI Automation, warm process already started once) |
| Open lesson document into the tree | 3 s | 0.159 s |
| Axes and function insert | 8 s | 2.109 s worker, 3.383 s UI |
| 3D insert | 20 s (revise over 30 s) | 5.966 s worker, 5.216 s UI |
| reference-60 preview | 45 s (revise over 67.5 s) | 8.574 s phase 1 file; 4.926 s phase 2 file. Phase 2 `within_relative_cap` is false against the Linux-relative 4.77 s figure. The spec absolute cap is 67.5 s. |
| Partial rerender | 3 s | 2.581 s |
| reference-60 1080p30 MP4 | 3 min (revise over 270 s) | 22.018 s phase 5; 27.416 s phase 1 file |
| lesson-60 GIF, headless | no 3 min gate | 15.815 s, 854×480, 60.0 s |
| Export progress label | 200 ms | 91.2 ms on the Journey A click |

## Follow-up items

1. **Windows teardown via Job Objects. Fixed in source. Reap tests written, execution deferred.** On Windows the supervisor assigns each Manim child to a job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` unless `MATHIMATOR_WIN32_JOBS=0`. Cancel calls `TerminateJobObject`. The shell's `assign_supervisor` does the same for the supervisor, and `Worker` drop / `ensure_running` close that job before waiting. `ensure_running` calls `child.kill()` only when no job was assigned. `test_windows_job_objects_are_the_default` passed and only reads the flag; it does not create a job. `test_cancel_reaps_manim_latex_and_dvisvgm_within_one_second` and `test_quit_reaps_manim_latex_and_dvisvgm_within_one_second` start a `manim.exe` stand-in that then starts `latex.exe` and `dvisvgm.exe`, and assert all three are gone within 1 s. They stay skipped unless `MATHIMATOR_WIN32_TESTS=1`. The Rust test `supervisor_job_reaps_manim_latex_and_dvisvgm_within_one_second` is the shell-side version of that tree and is `#[ignore]`. `cargo test` reported 1 ignored, 0 failed. None of those three tests were executed on this PC. `docs/design-spec.md` sections 1, 6, and 7 already describe this rule. Linux and macOS still use the process group.

2. **UI job handshake. Already fixed before this pass.** `desktop/src/jobSession.ts` reserves the id before the command, buffers early `done`/`error`, and treats Cancel before the id exists as a pending cancel. Vitest: `applies a done event that arrived before the export id was stored`, `remembers cancel that happens before the export command is sent`. 8 passed in `desktop/src/jobSession.test.ts`.

3. **Stale playback. Already fixed before this pass.** A dirty rebuild clears `output`. `playbackAt` uses the same file-exists rule as scrub, and the preview seeks when the chosen time changes by more than 0.001 s. Vitest: `does not play the previous movie once a beat is dirty`, `uses a blank canvas when the partial file is missing`, `seeks for a scrub smaller than a quarter second`.

4. **Handoff race. Already fixed before this pass.** The supervisor holds the state lock across clear, take-pending, and install, and joins a replaced preview for 1 s (`test_stop_process_joins_for_one_second`). `test_queued_preview_starts_once_after_export` asserts two Manim argv lines, so a second start in the gap would fail that count. It does not snapshot processes. The cancelled-preview partial is covered by `test_cancelled_render_drops_the_unfinished_partial`.

5. **Export `current` flag. Already fixed before this pass.** An older export sets the note "This file is from an older export." A preview does not replace "Rendering export". Vitest: `labels a finished file that is not the current export`, `does not replace Rendering export when a preview is reserved`.

6. **Formula anchor and shift. Already fixed before this pass.** `sourceAnchor`, `sourceShift`, `destinationAnchor`, and `destinationShift` are stored per formula. Shared `anchor`/`shift` still load. `test_formula_anchor_and_shift_are_stored_per_formula` saves, reopens, and checks both the objects (`[1, 0]` / `edge_up` versus `[0, 2]` / `edge_down`) and the template params.

7. **Delete object, clear cache, LaTeX error text. Already fixed before this pass.** Delete object removes the object and the beats that target it as one undo step (`test_delete_object_removes_targeting_beats_as_one_undo`, `test_deleting_a_transform_source_removes_the_destination`). Clear cache keeps `project.json` and `exports/` (`test_clear_cache_keeps_project_and_exports`). A LaTeX-looking failure is "LaTeX failed." plus the TeX log line (`test_latex_failure_includes_the_tex_log_line`). The window shows "Delete object" and "Clear cache".

8. **Reserved object ids. Already fixed before this pass.** Object ids that are Python keywords or generated scene names are rejected at load. `test_object_ids_reject_keywords_and_generated_names` uses `class` and `np`.

## Issues

| Severity | Issue | Repro | Status |
| --- | --- | --- | --- |
| High | GIF (and any later concat) fails after a preview is cancelled mid-animation. Manim leaves a short partial, the next run logs "Using cached data", and `combine_files` raises `InvalidDataError` (`Errno 1094995529`). Journey B's lesson project had `a1fbb270….mp4` and `MainScene.mp4` at 48 bytes. | Open lesson-60, edit, start the preview, export GIF before that preview finishes. | Fixed in source. `discard_unfinished_partials` deletes partials, the scene movie, and the manifest touched by the killed run, and keeps partials that were already logged as written. `test_cancelled_render_drops_the_unfinished_partial`. The already-built exe still has the old bundled worker, so the GUI was not re-run after the fix. |
| High | Transparent WebM is opaque. Corner alpha 255, pixel format yuv420p, codec vp9, `opaque_surface` true. The PNG sequence in the same phase 5 record passes (corner alpha 0). | `tests/test_phase5_render.py::test_surface_transparent_webm_and_png_sequence` with `--format webm --transparent`. | Not fixed. Needs a spec revision before any FFmpeg binary. The spec still requires the alpha check. |
| High | Job Object cancel and quit are implemented and not proven on this PC. The Windows default now assigns the job. This run forced `MATHIMATOR_WIN32_JOBS=0`. | `tests/test_win32_job.py` and the ignored Rust test. | Execution deferred until Craig approves running the APIs here. |
| Medium | Seven tests that walk the process table or call `process_alive` were deselected this session so they would not call kernel32. | Names listed in the command below. | Not run. They had passed in an earlier full run before this restriction. |
| Low | One MiKTeX `latex` sat near idle for many minutes and a GIF test failed only because that process was stopped. The same compile under `CREATE_NO_WINDOW` finished in about 1 s on retry. | Not reproduced. | Not a product change. |
| Low | Notices "Close" was clicked and the panel was still visible on the next screenshot. | `13-3d-refused.png`. | Not confirmed as a product bug. The click may have missed. The notices text itself is correct. |
| Info | Phase 2 preview 4.926 s is over the Linux-relative 4.77 s comparison and under the spec's 67.5 s absolute cap. | `out/phase2/timings.json`. | Not a spec failure. |
| Info | WebView2 offline installer was measured at 214,156,024 bytes in phase 5, against the spec's ~127 MB planning figure. | Phase 5 report. | Flag only. The spec has no size gate. |

## Commands

Win32 Job Object reap tests and the seven process-table tests were not run. `MATHIMATOR_WIN32_TESTS` was unset. `MATHIMATOR_WIN32_JOBS=0` and `MATHIMATOR_SKIP_PROCESS_TABLE=1` were set for the pytest process. `tests/conftest.py` also forces the job off unless the reap suite is selected.

- `tests/test_followup.py`: 11 passed in 3.56 s. Includes `test_windows_job_objects_are_the_default` and `test_cancelled_render_drops_the_unfinished_partial`.
- `npm test` (vitest): 8 passed in `desktop/src/jobSession.test.ts`.
- `npm run build` (`tsc --noEmit && vite build`): exit 0.
- `cargo test` (no `--ignored`): 0 passed, 0 failed, 1 ignored. The ignored test is `supervisor_job_reaps_manim_latex_and_dvisvgm_within_one_second`. `MATHIMATOR_WIN32_JOBS=0`.
- `cargo build`: exit 0, no warnings.
- `npm run tauri build` from the earlier pass: exit 0. NSIS installer 381.62 MiB. That installer still has the older bundled worker.
- Full pytest, kernel32 tests deselected, MiKTeX and bundle Typst prepended to PATH, `MATHIMATOR_WIN32_JOBS=0`, `MATHIMATOR_SKIP_PROCESS_TABLE=1`:

```
pytest tests -q --tb=line -ra -k "not process_group_kills and not cancel_ends_grandchild and not expression_rejection_does_not_spawn and not scrub_command_does_not_spawn and not preview_waits_behind_export and not cancels_the_in_flight and not existing_export_does_not_spawn"
```

Result: 1 failed, 87 passed, 3 skipped, 7 deselected in 187.72 s. The failure is `test_surface_transparent_webm_and_png_sequence` (`corner_alpha=255`). The PNG checks in that test ran before the WebM assert. The three skips are the Linux offline test and both Win32 job tests. The extra pass against the previous 86 is `test_windows_job_objects_are_the_default`.

Skipped by that command's environment, not by the `-k` filter: the Linux offline `unshare` test, and both tests in `tests/test_win32_job.py`.

Deselected on purpose: `test_process_group_kills_a_child_within_one_second`, `test_cancel_ends_grandchild_within_one_second`, `test_expression_rejection_does_not_spawn_manim`, `test_scrub_command_does_not_spawn_manim`, `test_preview_waits_behind_export_and_edits_do_not_cancel_it`, `test_a_new_preview_cancels_the_in_flight_preview`, `test_existing_export_does_not_spawn_manim`.

Earlier, with the bundle present: `tests/test_phase5_offline.py` was 2 passed, 1 skipped. The skipped test is the Linux network-namespace case. The Windows test isolates PATH to the bundled TinyTeX. It does not prove the NIC was disconnected.

## Screenshot artifacts

Under `out/screenshots/` (gitignored):

- `01-empty-ready.png` empty project, Ready
- `02-renamed.png` "Sine introduction"
- `03-journey-a-axes.png` axes document at 0.0 s (Create has not drawn yet)
- `04-journey-a-formula.png` formula field, Typst glyph, caption
- `05-journey-a-scrub.png` playhead 2.08 s, axes visible
- `06-journey-a-export-panel.png` MP4 1920×1080 30 fps, transparency unavailable
- `07-journey-a-exporting.png`, `08-journey-a-exported.png`
- `frame-journey-a-export-3.5s.png` sine curve, 1920×1080
- `frame-journey-a-export-5.5s.png` curve plus `y = sin(x)`
- `frame-journey-a-preview-3.5s.png` 854×480 preview
- `09-journey-b-opened.png` through `11d-journey-b-gif-done.png` lesson edits, undo, GIF failure text
- `12-notices.png`, `13-3d-refused.png` notices and the 3D refusal
- `14-journey-c-3d.png`, `15-journey-c-playing.png`
- `16-journey-c-webm-panel.png`, `16b-journey-c-png-panel.png`
- `17-journey-c-exporting.png`, `18-journey-c-exported.png`
- `frame-journey-c-preview-3s.png` axes and surface
- `frame-journey-c-png.png` transparent PNG frame (checkerboard in the viewer is alpha 0)
