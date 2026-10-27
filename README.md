# Mathimator

Mathimator is a Windows desktop app for one short math animation. A teacher or
a YouTube explainer builds a visual document, objects plus an ordered timeline
of beats, and Mathimator compiles it into a
[Manim Community](https://www.manim.community/) scene. The app previews the
scene as it changes and exports an MP4, WebM, GIF or PNG sequence, or the
generated Python. Making the animation takes no Python.

The objects are axes with a plotted function, formulas, text, shapes (a dot,
line, arrow, circle, rectangle or point on axes) and a 3D surface or curve.
Each beat is one animation: a wait, an entrance, an exit, a change such as a
move, scale, rotation or new colour, an emphasis such as indicate or wiggle, a
morph of one object into another, or a camera move in a 3D scene. A beat can
act on several objects at once, every beat but a wait takes an easing, and an
object can leave the screen and enter again.

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
| `ci/` | The helper scripts the GitHub Actions workflows run, and the tests that hold the workflows and the project documents to the code |
| `.github/` | The GitHub Actions workflows, the Dependabot configuration, the code owners, and the issue and pull request templates |
| `docs/` | The design spec |

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
| none | `.venv` | `python-tests` on Linux, with Python 3.13.17 and 3.14.9, and `python-tests-windows` |
| `typst` | `typst` 0.15.0 on PATH; skipped without it | `typst` |
| `process_table` | The process table: the kernel32 helper on Windows, `/proc` on Linux | `python-tests-windows` |
| `render` | Real Manim renders, and LaTeX on PATH for formulas | `render`, in the Slow workflow |
| `export` | Like `render`: the 1920×1080, 30 fps export of the reference scene | `render`, in the Slow workflow |
| `offline` | A built bundle, with the network blocked | `render`, in the Slow workflow |

The fast lane, which `python-tests` runs on Linux, with the `ci/` tests that
`python-lint` runs:

```powershell
uv run pytest tests scripts ci -m "not render and not export and not offline and not typst and not process_table"
```

On Linux, `bash ci/install-typst.sh DIR` installs the pinned Typst into `DIR`
for the `typst` tests, as the `typst` job does; the
[Linux notes](CONTRIBUTING.md#linux) show how.

`uv run pytest` on its own runs every test in `tests/`, the slow tiers
included. The render and export tests need LaTeX on PATH, and the offline tests
a built bundle (see [Building the installer](#building-the-installer)). On
Windows the bundle's TinyTeX and Typst serve all three, as the Slow
workflow's `render` job runs them:

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

`npm run test:ci` runs the tests with coverage, as the `ui` job does in CI, and
writes `desktop/coverage/` and `desktop/reports/junit-vitest.xml`.

The shell, from `desktop/src-tauri/`, after `npm run build`:

```powershell
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
```

On Windows, `cargo test --locked -- --include-ignored` also runs the shell's
ignored tests, as the `rust-windows` job does: `MATHIMATOR_WIN32_TESTS=1` lets
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

GitHub Actions runs the workflows in `.github/workflows` on GitHub's hosted
runners, `ubuntu-24.04` and `windows-2025`:

| Workflow | When it runs | What it does |
| --- | --- | --- |
| CI (`ci.yml`) | Every pull request, every push to `main`, and every `v*` tag through Release | Lints, tests and audits the code |
| Slow (`slow.yml`) | Every night on `main`, by hand from the Actions tab, and every `v*` tag through Release | Runs the render, export and offline suites and builds the installer |
| Release (`release.yml`) | A pushed `v*` tag | Checks the tag, runs CI and Slow, and drafts a GitHub Release |
| CodeQL (`codeql.yml`) | Every pull request, every push to `main`, and weekly | Scans the Python, TypeScript, Rust and workflow code |

The jobs of CI:

| Job | Runner | What it does |
| --- | --- | --- |
| `changes` | Linux | Picks the lanes that a pull request needs, with `ci/changed_lanes.py` |
| `python-lint` | Linux | The version and pin check, Ruff, mypy for win32 and linux, `uv lock --check`, the worker package build and the `ci/` tests |
| `hygiene` | Linux | The pre-commit hooks that no other job runs: the whitespace, line-ending, merge-conflict, large-file, YAML, TOML and JSON checks, and the schema checks of the workflows and the Dependabot configuration |
| `ui` | Linux | ESLint, tsc, Prettier, vitest with coverage, and the production build |
| `rust-linux` | Linux | rustfmt, Clippy and cargo test |
| `rust-windows` | Windows | rustfmt, Clippy and cargo test with the ignored tests: the Job Object test and the tests that start the real worker |
| `python-tests` | Linux | The fast lane on Python 3.14.9 and 3.13.17, with the worker's coverage |
| `python-tests-windows` | Windows | The fast lane with the Win32 tests, and the `ci/` tests |
| `typst` | Linux | The Typst tests, with the pinned Typst |
| `audit-python`, `audit-npm`, `audit-rust` | Linux | pip-audit of `uv.lock`, npm audit of the UI's runtime packages, and cargo-deny of the shell's crates with `ci/deny.toml` |
| `result` | Linux | The `CI result` check, which passes when every other job passed or was skipped |

`changes`, `python-lint`, `hygiene` and `result` run every time. Each other job
belongs to a lane, and in a pull request a lane runs only when the change
touches a file that its jobs test, lint or read:

- `python-tests`: the worker, the tests, the scenes, the spec, the scripts,
  the packaging files and the Python dependencies and pins;
- `python-tests-windows`: the same files, the `ci/` tests and
  `ci/windows-tools.ps1`;
- `typst`: the worker, the tests, the Python dependencies and pins, the Typst
  pin and `ci/install-typst.sh`;
- `ui`: the UI, the shell's JSON configuration, and the shared fixtures and
  worker messages that its tests read;
- `rust-linux`: the shell and the fixtures its tests embed. `rust-windows`
  also runs for a change to the worker, `.python-version` or
  `ci/windows-tools.ps1`;
- each audit: its lockfile and manifest, and `ci/deny.toml` for
  `audit-rust`.

A lane that a pull request does not need shows as skipped. A change to
`ci.yml` or `ci/changed_lanes.py` runs every lane, and so does every push to
`main` and every tag. A new push to a pull request cancels its running checks.
`ci/test_pipeline.py` holds the lanes to the files their jobs read, and the
workflows to the scripts, pins and paths they name.

Linux never compiles the shell's `cfg(windows)` code; `rust-windows` does. The
render, export and offline suites and the installer build are slow, so no pull
request runs them. The Slow workflow runs them every night, for every tag, and
whenever someone starts it on a branch from the Actions tab, as a change to
rendering, export, the bundle or its pins should. A tag runs every job of CI
and Slow and drafts a release, as [CONTRIBUTING.md](CONTRIBUTING.md)
describes.

The jobs upload their JUnit reports, and the worker's and the UI's coverage,
as workflow artifacts. Coverage is reported, never enforced.

CodeQL's alerts show in the repository's Security tab. GitHub's secret
scanning and push protection check every push, and the gitleaks pre-commit
hook checks each commit before it is made. Every week Dependabot
(`.github/dependabot.yml`) opens grouped pull requests for the dev tools in
`uv.lock`, the npm packages, the Rust crates and the actions the workflows
use. It never moves the versions that the spec pins.

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
[CONTRIBUTING.md](CONTRIBUTING.md) before you open a pull request.
[CHANGELOG.md](CHANGELOG.md) lists the changes in each release.
