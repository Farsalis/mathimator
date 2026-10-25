# Phase 1 report — walking skeleton

> Historical record. Where it differs from the current branch, [design-spec.md](design-spec.md) describes the current behaviour, and section 8 of [architecture-audit-report.md](architecture-audit-report.md) the remediation status.

Date: 21 October 2026.

Phase 1 gate only. Phase 2 was not started. No pull request was opened.

These numbers are from a Linux VM. They are not a budget result. The reference machine in section 2 is Windows 11, 8 cores, 16 GB RAM, SSD, integrated graphics, plugged in, High performance. This machine is none of those things, so the 45 second preview budget, the 3 minute export budget, and the 8 second launch budget are not judged here. The 1.5× revise rule was not applied. The spec was not revised.

## Machine

- CPU: Intel Xeon, family 6, model 207, reported as `Intel(R) Xeon(R) Processor`. 4 cores, 1 thread per core. KVM guest.
- RAM: 15 GiB.
- OS: Ubuntu 24.04.5 LTS, Linux 6.12.94+.
- Not the Windows reference machine.

## Versions

| Piece | Version |
| --- | --- |
| Tauri crate | 2.12.5 (`protocol-asset` enabled because the asset protocol is on) |
| Tauri CLI (`@tauri-apps/cli`) | 2.12.4 |
| `@tauri-apps/api` | 2.12.4 |
| React / React DOM | 19.3.1 |
| Vite | 8.3.5 |
| TypeScript | 5.9.3 |
| `@vitejs/plugin-react` | 6.1.2 |
| Rust | 1.99.1 |
| Python | 3.14.8 |
| Manim | 0.22.0 |
| PyAV (`av`) | 19.0.2, used only by the tests |

Stay on Tauri 2.12.x, React 19, and Vite 8.

`manim render --help` for 0.22.0 accepts `--max-inflight-encoders`, `--seed`, `--renderer cairo`, `--fps`, `-r`, `--format mp4`, `--media_dir`, and `--progress_bar none`. The section 5 flag names were used as written. `--preview` and `--live-preview` exist and were not passed.

## Measurements

Cold partial movies means `out/phase1/media/videos` was deleted before the timed preview. TeX output under that media directory was already warm for the pytest number. Fonts were not a first-boot TinyTeX. None of this is the reference-machine procedure.

| Check | Result |
| --- | --- |
| Preview wall clock | 3.180 s. Command to `done` on an earlier run in the same media directory, before that directory had a TeX cache, was 4.478 s. |
| Preview file | 854×480, duration 60.0 s (stream time base). Tolerance is 1/15 s. |
| Export wall clock | 16.863 s. |
| Export file | 1920×1080, duration 60.0 s. Tolerance is 1/30 s. The preview file was not reused. |
| Partial movies | 8, beat indexes 0 through 7, in that order. |
| Last-beat hashes | First 7 SHA-256 hashes unchanged. The 8th changed. |
| Cancel | 0.010 s. `latex` was in the Manim process group and was gone with it. A separate test also killed a `sleep` child of a session leader in under 1 s. |
| Second dev start | 3.318 s from process spawn until the 1080×760 window existed and the UI had recorded a worker `status` / `ready`. The window was up at 2.566 s; the worker answer landed at 3.316 s. |
| First `tauri dev` compile | 40.69 s to finish the dev binary, then the window opened. That compile is not the second-start measurement. |
| Window | Opened under Xvfb display `:99`. The window showed Mathimator, Ready, Render, and Export. Clicking Export set the button label to "Rendering export" before the render finished. Clicking Render replaced the empty stage with the preview player. |

Paths kept after the gate:

- Preview copy: `out/samples/reference-60-preview-854x480-15fps.mp4` (188 KB, 854×480, 60.0 s).
- Export copy: `out/samples/reference-60-export-1920x1080-30fps.mp4` (509 KB, 1920×1080, 60.0 s).
- The timed pytest files were under `out/phase1/media/videos/reference_60/480p15/MainScene.mp4` and `.../1080p30/MainScene.mp4`. A later full pytest deletes `out/phase1/media/videos` before it renders the preview again, so the export kept for inspection is the sample copy above.
- The window's own render used `out/media/videos/reference_60/` with the same argv shape.

Preview and export shared one media directory. Manim wrote `480p15` and `1080p30`. They did not overwrite each other.

Compared with the numeric targets only, and not as a budget result: preview 3.180 s is under 45 s, export 16.863 s is under 3 minutes, second start 3.318 s is under 8 s. This VM does not pass or fail those budgets.

## Tests

```bash
.venv/bin/python -m pytest tests -m 'not export' -q
```

13 passed, 1 deselected, in 5.77 s. The deselected test is the export.

```bash
.venv/bin/python -m pytest tests/test_phase1.py::test_export_1080p30 -q
```

1 passed in 16.97 s.

The cache test copies `scenes/reference_60_last_beat.py` onto a working file still named `reference_60.py` and renders it into the same media directory. Manim's video folder is keyed by the file stem, so the two handwritten files have to be rendered under one name for the partial-movie cache to be the same cache. The files differ only in the last color, `#F2CC8F` versus `#81B29A`.

PyAV 19 reports `av.time_base` as the integer `1000000`, not a fraction. Duration is taken from the video stream time base. Container duration divided by that integer is the same 60.0 s.

## How to run

Developer TeX is required (`latex` and `dvisvgm` on `PATH`, including the packages in Manim's default MathTex template: amsmath, amssymb, dsfont, and the rest of that preamble). This checkout used Ubuntu TeX Live packages, including `texlive-fonts-extra` for `dsfont.sty`. TinyTeX is phase 5 and is not vendored.

```bash
uv python install 3.14.8
uv venv --python 3.14.8 .venv
uv pip install --python .venv/bin/python -r worker/requirements-render.txt pytest
cd desktop && npm install && npm run build
```

`npm run build` creates `desktop/dist`. Tauri's compile checks that `frontendDist` path. `dist/` is gitignored.

Worker tests, from the repo root:

```bash
.venv/bin/python -m pytest tests -m 'not export' -q
.venv/bin/python -m pytest tests/test_phase1.py::test_export_1080p30 -q
```

The app, from `desktop/`:

```bash
npm run tauri dev
```

The shell starts the supervisor with `--scene`, `--media-dir`, and `--python`. Defaults are `scenes/reference_60.py`, `out/media`, and `.venv/bin/python`. Override with `MATHIMATOR_SCENE`, `MATHIMATOR_MEDIA_DIR`, `MATHIMATOR_PYTHON`, and `MATHIMATOR_LOG`.

On Linux without a display, `Xvfb` can host the window. `WEBKIT_DISABLE_DMABUF_RENDERER=1` was set for this VM.

## What phase 1 contains

- `desktop/` — Tauri 2.12 window, React 19, Vite 8. Render plays the preview MP4 through `convertFileSrc`. Export writes a 1920×1080, 30 fps MP4. The export button sets "Rendering export" in the click handler, before the supervisor returns.
- `worker/mathimator_worker/` — stdio supervisor. It does not import Manim or PyAV. Each render is a new `python -m manim` grandchild in its own session.
- `scenes/reference_60.py` — handwritten `MainScene`, seed 1, background `#000000`, durations 8, 10, 6, 4, 8, 8, 8, 8.
- `scenes/reference_60_last_beat.py` — the same scene with the last beat color changed. Cache test only.
- `scenes/runtime_error.py` — `MainScene.construct` raises `RuntimeError`.
- `LICENSE` — MIT. `Copyright (c) 2026 Mathimator contributors`.

Sample movies are under `out/` and are not committed.

## Protocol

Stdio, one UTF-8 JSON object per line.

Commands: `status`, `render_preview`, `export`, `cancel` (`jobId`).

Events: `ready`, `beat_ready` (`beatIndex`, `path`), `done` (`path`), `error` (`message`).

A cancelled job emits `error` with message `cancelled`. The supervisor stays up. A following `status` returns `ready`.

Phase 1 has no `project.json`. Phase 2 should feed the document into this same protocol and stop using `--scene` as the source of the scene. There is not a second protocol.

Preview argv:

```text
<python> -m manim render <scene.py> MainScene --renderer cairo --seed 1 -r 854,480 --fps 15 --format mp4 --media_dir <media_dir> --max-inflight-encoders 1 --progress_bar none
```

Export changes `-r` to `1920,1080` and `--fps` to `30`.

The supervisor logs `argv: ` plus a JSON list. It reads Manim's `Animation N` lines, checks that the partial file is under `partial_movie_files` in the media directory, and if a beat was missed reads `partial_movie_file_list.txt` before `done`.

## Divergences

1. The supervisor is started with `--scene`, `--media-dir`, `--python`, and optional `--log`. Phase 1 has no document model. Phase 2 replaces `--scene` with `project.json` and keeps the stdio commands.
2. While an export is running, another preview or export is refused with `export is already running`. An in-flight preview is cancelled when a new preview or an export is requested. A preview is not queued behind an export. Section 10 puts that waiting preview in phase 2.
3. There is no partial-movie index file, scrub path, or `render_frame` helper. Those are phase 2. `beat_ready` carries the beat index and the partial path.
4. The webview asset scope allows `$APPLOCALDATA/**` and `/workspace/out/**`, so `convertFileSrc` can read this checkout's `out/` directory and nothing else on disk.
5. Process control is Unix `start_new_session` plus `os.killpg`, with a `/proc` walk so a child that left the group is still killed. There is no Win32 code. Windows was not tested. If Windows cannot reap `latex` and `dvisvgm` without a Win32 API, that is a spec revision and was not attempted here.
6. TeX is a developer install from Ubuntu packages, not TinyTeX.
7. The Tauri npm CLI is 2.12.4. That was the newest CLI release while the crate was already 2.12.5. The Rust crate stays on 2.12.5.
8. uv does not publish CPython 3.14.9 for linux-x86_64. The newest 3.14 build it publishes is 3.14.8. `manim==0.22.0` installed on 3.14.8, so the 3.13 fallback was not used.

## Failures

None in the final test commands above. An earlier probe multiplied container duration by `av.time_base` and reported a nonsense length; the movie was already 60.0 s. That probe is fixed. The render itself did not fail.
