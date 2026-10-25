# Phase 5 report — presets and the Windows installer

> Historical record. Where it differs from the current branch, [design-spec.md](design-spec.md) describes the current behaviour, and section 8 of [architecture-audit-report.md](architecture-audit-report.md) the remediation status.

Date: 21 October 2026.

Phase 5 only. Nothing in section 9 was started. No pull request was opened. `docs/design-spec.md` was not revised.

These numbers are from a Linux VM. They are not a budget result. The reference machine in section 2 is Windows 11, 8 cores, 16 GB RAM, SSD, integrated graphics, plugged in, High performance. This machine is none of those things, so the Windows timing budgets were not judged. The 270 second revise rule was not applied to these Linux times, and the spec was not revised.

The phase 5 branch was cut from `origin/cursor/phase-4-templates-1504` at `ee6a2a18fd46236533af33a32cbd4ac3588be736`.

## Machine

Intel Xeon, 4 cores, Ubuntu, Linux 6.12.94+. Not the Windows reference machine. Not a clean Windows 11 VM.

## Versions

| Piece | Version |
| --- | --- |
| Dev and bundled Python | 3.14.8. uv 0.13.4 has no CPython 3.14.9 build for `linux-x86_64`. 3.13.17 was not used. |
| Manim | `manim[typst]==0.22.0` in the bundle. Scene codegen still emits `MathTex` only. |
| Typst | Bundled binary prints `typst 0.15.0 (3ae52774)`. Developer binary at `/usr/local/bin/typst` is the same version and is not on the bundle PATH. |
| TinyTeX | Release `v2026.10`. After `tlmgr update --self`, `tlmgr revision 80658 (2026-10-16 12:08:14 +0200)`. `latex` is pdfTeX 3.141592653-2.6-1.40.29 (TeX Live 2026). `dvisvgm` is 3.6. |
| Tauri | Crate 2.12.5, CLI 2.12.4. Unchanged from phase 4. |

`amssymb.sty` is inside the TeX Live package `amsfonts`, not a package named `amssymb`. The seed list installs `amsfonts`. The default MathTex template also needs `standalone`, `preview`, `babel`, `babel-english`, `amsmath`, and `dvisvgm`. A warmup `y = \sin(x)` compiled with the bundled `latex` and `dvisvgm` before the repository was pointed at an empty local directory. A later `tlmgr install qrcode` failed, which is the network-install lock.

## What phase 5 contains

- The Export button opens a preset panel. MP4 defaults to 1920×1080 at 30 fps. GIF defaults to 854×480 at 15 fps. WebM and PNG sequence default to 1920×1080 at 30 fps. Resolution stays in {854×480, 1280×720, 1920×1080}. Frame rate stays in {15, 30, 60}. Choosing a preset fills that preset's resolution and fps.
- Transparency is disabled for MP4 with "MP4 cannot store transparency." and for GIF with "GIF cannot store an alpha channel." WebM and PNG sequence can turn it on. The sentence then is "Transparency includes the background." `--format png` is not used. PNG sequence is `--format png-sequence`.
- The click that starts an export sets the button and the status to "Rendering export" before the supervisor answers. A second Export click while one runs shows "An export is already running." and does not start another job. An unchanged `exportKey` returns the existing file and does not start Manim. Preview stays 854×480, 15 fps, opaque. Preview files are not copied into `.mathimator/exports/`.
- Generated scene source is unchanged by the export profile. It still has no resolution and no fps.
- Third-party notices name Manim Community (MIT), Typst (Apache License 2.0), and TinyTeX (the TeX programs keep their own licenses). Mathimator's own source stays MIT. WebView2 is described as Microsoft software, not part of that MIT source. The notice text wraps in the inspector.
- `packaging/build-bundle.sh` builds `packaging/bundle/`: the uv environment, TinyTeX, the Typst 0.15.0 binary, the worker package, and `samples/reference-60/project.json`. The built tree is gitignored.
- `desktop/src-tauri/tauri.windows.conf.json` turns on an unsigned NSIS bundle for Windows only, with `webviewInstallMode` `offlineInstaller`. `tauri.conf.json` on Linux still has `bundle.active` false, so `tauri dev` on Linux does not try to make a deb or AppImage.

Absent, as specified: Mac and Linux product installers, ManimGL, OpenGL, a freeform Python editor, plugins, voiceover, a vendored FFmpeg binary, code signing, and Tectonic.

## Export checks

None of the times below are section 2 budget results.

| Export | Result |
| --- | --- |
| `lesson-60` GIF | 854×480, duration 60.0 s, 10.103 s wall time from the export command to `done`. `/tmp/mathimator-phase5/out/phase5/lesson-60/.mathimator/exports/9348895e5612d65f/scene.gif`. Transparency off. Not under `480p15`. |
| `reference-60` MP4 | 1920×1080, duration 60.0 s, 13.983 s wall time. `/tmp/mathimator-phase5/out/phase5/reference-60/.mathimator/exports/969ccfe211d12073/scene.mp4`. Preview beside it is 854×480, duration 60.0 s, and the SHA-256 values differ. |
| Second `reference-60` MP4 | Same path. The worker log gained `export cache hit` and no new `argv:` line. Manim was not started. |
| Transparent PNG sequence | 120 frames, 1280×720, 30 fps. `/tmp/mathimator-phase5/out/phase5/surface/.mathimator/exports/63d4964c6e6bdc90/frames`. Frame `0108.png`: corner alpha 0; 202812 opaque cyan face pixels; 4858 partial-alpha edge pixels. |
| Transparent WebM | File exists, 1280×720, duration 4.0 s. `/tmp/mathimator-phase5/out/phase5/surface/.mathimator/exports/bb291a6355e4b684/scene.webm`. Alpha check failed. See failures. |

The commands were `--renderer cairo` with `--format gif`, `--format mp4`, `--format webm --transparent`, and `--format png-sequence --transparent`. None contained `--preview`, `--live-preview`, `--renderer opengl`, or `--disable_caching`.

A direct PyAV encode with `libvpx-vp9` and `yuva420p` also came back as `yuv420p` with corner alpha 255. `auto-alt-ref=0` did not change that. No FFmpeg binary was added.

## Offline import

`unshare --net --map-root-user` made a connection to `1.1.1.1:443` fail with "Network is unreachable". Inside that namespace, with `PATH` led by the bundled TinyTeX bin and with `TEXMF*` variables unset:

- `python -c "import manim"` printed `0.22.0`
- `command -v latex` and `command -v dvisvgm` were the bundled binaries, and both `--version` commands exited 0
- `manim render` of a short `MathTex(r"y = \sin(x)")` scene wrote a non-empty `MainScene.mp4` using only that PATH

`typst --version` on `packaging/bundle/typst/typst` is `typst 0.15.0 (3ae52774)`.

## Sizes

| Tree | Size |
| --- | --- |
| `packaging/bundle` | 757,564,069 bytes |
| uv venv | 498,292,836 bytes |
| TinyTeX | 203,359,474 bytes |
| Typst binary | 55,735,392 bytes |
| `mathimator.exe` | 23,146,626 bytes |
| WebView2 offline installer | 214,156,024 bytes. The spec's planning figure of about 127 MB was not this file's size. |
| NSIS setup | 412,331,698 bytes. Tauri printed 393.23 MiB. |

## Installer

A cross build on this VM produced an unsigned NSIS installer:

`/tmp/mathimator-phase5/desktop/src-tauri/target/x86_64-pc-windows-gnu/release/bundle/nsis/Mathimator_0.1.0_x64-setup.exe`

`file` says it is a PE32 Nullsoft installer. `mathimator.exe` next to it is a PE32+ x86-64 Windows GUI binary. The generated `installer.nsi` sets `INSTALLWEBVIEW2MODE` to `offlineInstaller` and embeds `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`. Signing was skipped. Tauri printed that signing is only supported on Windows hosts and that cross-compilation is experimental.

That installer is not a Windows product install. The resources it copies are this VM's Linux bundle: `runtime\venv\bin\python`, Linux TinyTeX, and the Linux Typst binary. Windows cannot run that Python. The Windows TinyTeX asset is `TinyTeX-1-windows-v2026.10.exe`, and this script does not run it. The first cross check failed with `NotAttempted("x86_64-w64-mingw32-windres")` until `binutils-mingw-w64` was installed. After that, the NSIS file above was produced.

The clean-machine test was not run. This VM is not a Windows 11 account with no Python, FFmpeg, TeX, or Typst, and the network was not disconnected for an install. No claim is made that a clean install succeeded.

## Tests

```bash
/tmp/mathimator-phase5/.venv/bin/python -m pytest tests -q --tb=line
```

83 passed, 1 failed, in 151.36 s.

The failure is `tests/test_phase5_render.py::test_surface_transparent_webm_and_png_sequence`. The PNG alpha checks in that test passed. The assertion that fails is the WebM corner alpha, which is 255. The message includes the Manim command above.

The passing phase 5 checks include the export argv matrix, the MP4 and GIF transparency refusals, GIF and WebM no longer stopping with "This phase exports MP4 only.", an unchanged GIF `exportKey` that does not spawn Manim, the `reference-60` MP4 cache hit, notices text, bundled Typst 0.15.0, and the offline `manim` / `latex` / `dvisvgm` / MathTex render.

Phase 1 through 4 tests are in that same command, including the phase 1 cancel test. Journey A codegen is still the section 5 block.

## Launch

```bash
Xvfb :98 -screen 0 1280x800x24
cd desktop
DISPLAY=:98 WEBKIT_DISABLE_DMABUF_RENDERER=1 GDK_BACKEND=x11 \
  MATHIMATOR_PYTHON=../.venv/bin/python \
  npm run tauri dev
```

The window opened at 1180×800 on an empty project. Status was Ready. No separate Manim or OpenGL window was present.

1. Export opened the preset panel on MP4, 1920×1080, 30 fps. The transparency control was disabled. The text was "MP4 cannot store transparency."
2. GIF filled 854×480 and 15 fps. The control stayed disabled. The text was "GIF cannot store an alpha channel."
3. WebM filled 1920×1080 and 30 fps. The control was enabled. The text was "Transparency includes the background."
4. PNG sequence did the same, with the control enabled.
5. Notices showed Manim, Typst, TinyTeX, WebView2, and MIT. The lines wrap in the inspector.
6. Sample opened the reference-60 project as "Sine introduction", eight beats, 60.0 s, with "Preview is out of date". The canvas stayed a black opaque frame. There is no checkerboard.
7. Starting export changed the button and the status to "Rendering export" and showed Cancel export. A second click while that job was running showed "An export is already running."
8. The `lesson-60` GIF was the pytest file above, not a second render from this window.

Native selects were white with light text until `color-scheme: dark`. After that, the preset values were readable. That CSS change is in this phase.

## Divergences

1. The Windows timing budgets were not judged. Linux GIF wall time was 10.103 s and Linux MP4 wall time was 13.983 s. Those are not pass or fail results for section 2.
2. uv publishes no CPython 3.14.9 for linux-x86_64. The bundle uses 3.14.8. The Manim pin stayed `0.22.0`.
3. Transparent WebM does not keep alpha with PyAV 19.0.2 and libvpx-vp9. The file is `yuv420p`. PNG sequence alpha works. No FFmpeg binary was added, and the spec was not revised.
4. The NSIS file embeds the Linux runtime built here, plus the WebView2 offline installer. It was not installed on Windows.
5. The downloaded WebView2 installer is 214,156,024 bytes. That is the measured file, not the spec's planning figure of about 127 MB.
6. `tlmgr` from TinyTeX v2026.10 refused package installs until `tlmgr update --self`. `amssymb` is not its own tlmgr package name.

## Failures

Transparent WebM alpha is a failed gate. The pytest command above reports that failure and no others. The clean-machine install was not run, so it was neither passed nor failed. Phase 6 was not started.
