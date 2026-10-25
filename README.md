# Mathimator

Mathimator is a Windows desktop app for one short math animation. A teacher or
a YouTube explainer builds a visual document, objects plus an ordered timeline
of beats, and Mathimator compiles it into a
[Manim Community](https://www.manim.community/) scene. The app previews the
scene as it changes and exports an MP4, WebM, GIF or PNG sequence, or the
generated Python. Making the animation takes no Python.

[docs/design-spec.md](docs/design-spec.md) is the design and its acceptance
criteria. Mathimator v1 targets Windows 11 x64. The fast tests and the Rust
pre-checks also run on Linux; macOS is untested.

## How it works

Mathimator runs as three processes:

- **UI**: React 19 and TypeScript, built with Vite, in the Tauri webview
  (`desktop/src`). It shows the document as the worker last reported it, and
  owns the selection, the drafts and the playhead.
- **Shell**: the Tauri 2 app in Rust (`desktop/src-tauri`). It starts the
  worker, forwards the UI's commands to it, and passes its replies and events
  back to the UI. It also finds the bundled runtime, keeps the logs, checks
  that TeX works, and renders the equation editor's live glyph with Typst.
- **Worker**: a Python supervisor that uses only the standard library
  (`worker/mathimator_worker`). It owns the document, undo and redo, the
  project folder and the render queue. It generates the scene code and runs
  each render as a new Manim process. On Windows that process goes into a
  kill-on-close Job Object, so Cancel also ends LaTeX and dvisvgm.

The shell and the worker exchange one JSON object per line over the worker's
stdin and stdout. The worker's reply to the shell's first request names its
protocol version, and the shell refuses a worker that speaks another. Frames
and movies cross every hop as file paths, never as bytes. Section 6 of the spec
describes the protocol.

| Path | Contents |
| --- | --- |
| `desktop/` | The UI, with its Vite, TypeScript, ESLint and Prettier configuration |
| `desktop/src-tauri/` | The Rust shell |
| `worker/` | The worker package, `mathimator_worker` |
| `tests/` | The pytest suite. `tests/fixtures/` holds the sample projects and the fixtures that the Python, Rust and TypeScript tests share |
| `scenes/` | Scene code that the tests render or compare byte for byte |
| `packaging/` | The bundle build scripts, and `versions.env`, which pins every bundled tool with its SHA-256 |
| `scripts/` | The version check, the bundle check and the notices generator |
| `ci/` | The GitLab CI jobs that `.gitlab-ci.yml` includes, and their helper scripts |
| `docs/` | The design spec, the audit reports and the phase reports |

## Quick start

Install these first:

- [uv](https://docs.astral.sh/uv/) 0.13.5. `pyproject.toml` requires exactly
  this release, and any other uv refuses to run in the repository. Install it
  with `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/0.13.5/install.ps1 | iex"`,
  or move an existing uv to it with `uv self update 0.13.5`.
- Node.js 24, the major `desktop/.nvmrc` names. npm refuses any other.
- [rustup](https://rustup.rs/). It installs Rust 1.93.0 with rustfmt and
  Clippy, as `desktop/src-tauri/rust-toolchain.toml` pins them.
- On Windows, Tauri's prerequisites: the Microsoft C++ Build Tools with
  "Desktop development with C++", and the WebView2 runtime, which Windows 11
  includes.

On Linux, read the [Linux notes](CONTRIBUTING.md#linux) in CONTRIBUTING.md
before you sync. Then, from the repository root:

```powershell
uv sync --locked
cd desktop
npm ci
npm run build
cd src-tauri
rustup toolchain install
```

`uv sync --locked` creates `.venv` with CPython 3.14.9 (`.python-version`), the
worker, the dev tools and the Manim render stack, exactly as `uv.lock` pins
them. `npm run build` type-checks the UI and writes `desktop/dist`, which every
cargo build embeds, so run it before any cargo command. `rustup toolchain
install` installs the toolchain that the file in its working folder names.

## Running the app

```powershell
cd desktop
npm run tauri dev
```

This starts Vite and a debug build of the shell. A debug shell runs the worker
from `worker/` with the `.venv` interpreter, and Sample copies
`tests/fixtures/reference-60/project.json` into a new temp project.

Formulas and axis labels need LaTeX. A debug shell uses the TinyTeX of a built
bundle (`packaging/bundle`) when there is one, and otherwise the `latex` and
`dvisvgm` on PATH. The equation editor's glyph preview needs Typst 0.15.0,
again from the bundle or from PATH. Without them the rest of the app works.
[CONTRIBUTING.md](CONTRIBUTING.md) lists the variables that point a debug shell
at other tools.

The shell writes `shell.log` and `supervisor.err` to `out/logs` in a debug
build, and to `%LOCALAPPDATA%\com.mathimator.desktop\logs` when installed. The
worker writes `worker.log` to the project's `.mathimator\logs` folder.

## Tests

pytest markers sort the Python tests into tiers:

| Marker | What the tests need | CI job |
| --- | --- | --- |
| none | `.venv` | `test:python` on Linux, with Python 3.13.17 and 3.14.9, and `test:windows` |
| `typst` | `typst` 0.15.0 on PATH; skipped without it | `test:typst` |
| `process_table` | The process table: the kernel32 helper on Windows, `/proc` on Linux | `test:windows` |
| `render` | Real Manim renders, and LaTeX on PATH for formulas | `test:render:windows` |
| `export` | Like `render`: the 1920×1080, 30 fps export of the reference scene | `test:render:windows` |
| `offline` | A built bundle, with the network blocked | `test:render:windows` |

The fast lane, which `test:python` runs on Linux, with the `ci/` tests that
`lint:repo` runs:

```powershell
uv run pytest tests scripts ci -m "not render and not export and not offline and not typst and not process_table"
```

On Linux, `bash ci/install-typst.sh DIR` installs the pinned Typst into `DIR`
for the `typst` tests, as `test:typst` does; the
[Linux notes](CONTRIBUTING.md#linux) show how.

`uv run pytest` on its own runs every test in `tests/`, the slow tiers
included. The render and export tests need LaTeX on PATH, and the offline tests
a built bundle (see [Building the installer](#building-the-installer)). On
Windows the bundle's TinyTeX and Typst serve all three, as
`test:render:windows` runs them:

```powershell
$runtime = Get-Content -Raw packaging/bundle/runtime.json | ConvertFrom-Json
$env:PATH = "$PWD\packaging\bundle\$($runtime.tinytexBin.Replace('/', '\'));$PWD\packaging\bundle\typst;$env:PATH"
uv run pytest tests -m "render or export or offline"
```

The Job Object tests create real Job Objects, so they run only when
`MATHIMATOR_WIN32_TESTS=1` is set, as the Windows CI jobs set it.

The UI, from `desktop/`:

```powershell
npm test
npm run lint
npm run typecheck
npm run format:check
```

`npm run test:ci` runs the tests with coverage, as `test:ui` does in CI, and
writes `desktop/coverage/` and `desktop/reports/junit-vitest.xml`.

The shell, from `desktop/src-tauri/`, after `npm run build`:

```powershell
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
```

On Windows, `cargo test --locked -- --include-ignored` also runs the shell's
ignored tests, as `test:rust:windows` does: `MATHIMATOR_WIN32_TESTS=1` lets
the Job Object test run, and `MATHIMATOR_REAL_WORKER_TESTS=1` the tests that
start the repository's `.venv` Python, the worker among them.

The Python lint, type and version checks, from the repository root:

```powershell
uv run ruff check
uv run ruff format --check
uv run mypy
uv run mypy --platform linux
uv run python scripts/check_versions.py
```

## Building the installer

The installer is an unsigned NSIS installer for Windows that installs for all
users under Program Files, so installing needs administrator rights. It
embeds Microsoft's WebView2 offline installer and runs it on a machine that
lacks the WebView2 runtime. It ships `packaging/bundle` as its `runtime`
folder: a relocatable CPython with the render stack from `uv.lock`, TinyTeX,
Typst, the worker, the sample project, and `runtime.json`, which tells the
shell where each of them is.

On Windows, with Node and Rust on PATH, one script builds the installer as a
release does. Run it in Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ci/package-windows.ps1
```

It runs these steps, which can also be run one at a time:

1. `packaging/build-bundle.ps1` builds `packaging/bundle`. It downloads uv,
   CPython, TinyTeX and Typst into `packaging/.cache`, checks each download
   against its SHA-256 in `packaging/versions.env`, compiles the bundled
   Python and the worker to checked-hash bytecode, which an install under
   Program Files cannot write for itself, and logs to `out/bundle.log`. Run it
   in Windows PowerShell:
   `powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build-bundle.ps1`.
2. `npm ci` in `desktop/`, and `cargo fetch --locked` in `desktop/src-tauri/`.
3. The bundled interpreter runs `scripts/generate_notices.py`, which writes
   `THIRD_PARTY_NOTICES.txt` into the bundle, and then
   `scripts/check_bundle.py`, which fails on a bundle that is stale,
   incomplete or built for another platform.
4. `npm run tauri build` in `desktop/` makes the installer. After its cargo
   build it runs both scripts of step 3 itself, as its `beforeBundleCommand`,
   so it fails on a missing or stale bundle even when you run it on its own.
5. `out/package` receives the installer, `THIRD_PARTY_NOTICES.txt`, and
   `SHA256SUMS` for both.

`packaging/build-bundle.sh` runs the PowerShell script from Git Bash, MSYS2 or
Cygwin. On Linux it builds a linux-x86_64 runtime for the tests. There is no
Linux installer, and `scripts/generate_notices.py` refuses a Linux bundle,
because it lists only the libraries that the Windows CPython links in.

## Continuous integration

The pipeline in `.gitlab-ci.yml` and `ci/` runs on gitlab.com's hosted runners
and needs only the Free tier. That tier gives the namespace about 400 compute
minutes a month, so the pipeline runs a job only where a change needs it. It
runs for merge requests, the default branch, tags and the Renovate schedule;
there is no nightly pipeline.

| Stage | Hosted Linux runners | Hosted Windows runner |
| --- | --- | --- |
| build | `build:ui` | |
| lint | `lint:repo`, `lint:python`, `lint:ui` | |
| test | `test:python`, `test:typst`, `test:ui`, `test:rust`, `audit:python`, `audit:npm`, `audit:rust` | `test:windows`, `test:rust:windows`, `test:render:windows` |
| package | | `package:windows` |
| release | `release:notes`, `release:upload`, `release:publish` | |
| maintain | `renovate` | |

Every merge request runs `lint:repo` (the version and pin check, Ruff, the
`ci/` tests and the remaining pre-commit hooks), GitLab SAST and Secret
Detection. The other jobs run in a merge request only when it changes what
they check, or the CI files that define them:

- `lint:python` and `test:python`: the worker, the tests, the scenes, the
  spec, the scripts, the packaging files and the Python dependencies and
  pins;
- `test:typst`: the worker, the tests, the Python dependencies and pins, the
  Typst pin and `ci/install-typst.sh`;
- `build:ui`, `lint:ui` and `test:ui`: the UI and the shared fixtures and
  worker messages its tests read;
- `build:ui` and `test:rust`: the shell and the fixtures its tests embed. The
  shell's JSON configuration also runs `lint:ui`;
- each audit: its lockfile and manifest, and `ci/deny.toml` for
  `audit:rust`;
- `test:windows`: the worker, the tests, the scenes, the scripts, the `ci/`
  tests and the Python dependencies and pins.

Merges are semi-linear, so the default branch runs only `lint:repo`, SAST and
Secret Detection. The Windows Rust build (`test:rust:windows`), the render,
export and offline suites (`test:render:windows`) and the installer
(`package:windows`) run on tags, and otherwise only when someone starts them
as manual jobs. A tag runs every job, and a protected `vX.Y.Z` tag publishes a
release, as [CONTRIBUTING.md](CONTRIBUTING.md) describes.

Test coverage of the worker and the UI is reported, never enforced.

## Licence

Mathimator's own source is under the MIT licence in [LICENSE](LICENSE). The
installer also ships third-party software under its own licences: CPython and
the Manim render stack, TinyTeX, Typst, the Rust crates and npm packages the
app is built from, and Microsoft's WebView2 offline installer, which is
Microsoft software under Microsoft's own terms. PyAV's FFmpeg libraries in the
render stack include the GPL-licensed x264 and x265 encoders.
`THIRD_PARTY_NOTICES.txt`, which `scripts/generate_notices.py` writes from the
built bundle, covers the bundled runtime, the Rust crates and the npm packages,
each with its licence, names WebView2 as Microsoft software under Microsoft's
terms, and includes a written offer of the FFmpeg source. It ships with the
installer and with every release, and the app's Notices panel shows it.

Report security problems as [SECURITY.md](SECURITY.md) describes, and see
[CONTRIBUTING.md](CONTRIBUTING.md) before you open a merge request.
[CHANGELOG.md](CHANGELOG.md) lists the changes in each release.
