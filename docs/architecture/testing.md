# Testing

Mathimator has three test suites, one per language, and they share one set of
fixtures so that a change one side makes and another does not follow fails a
test:

| Suite | Runner | Where |
| --- | --- | --- |
| Worker, document, codegen, protocol, rendering | pytest | [`tests`](../../tests), plus [`scripts`](../../scripts) and [`ci`](../../ci) for their own scripts |
| UI: rule modules, hooks and components | vitest, in Node or jsdom | `desktop/src/**/*.test.ts(x)`, beside each module |
| Shell | cargo test | `#[cfg(test)]` modules in [`desktop/src-tauri/src`](../../desktop/src-tauri/src) |

[README.md](../../README.md#tests) has the commands, and CI runs all of them.
This page explains how the suites are organised and what each tier needs.

## Python test tiers

pytest markers, registered in [`pytest.ini`](../../pytest.ini), sort the slow
or platform-bound tests into tiers. `--strict-markers` refuses an unknown one.

| Marker | What the tests need | CI job |
| --- | --- | --- |
| none | The `.venv` from `uv sync`; no Manim process | `python-tests` (Linux) and `python-tests-windows` |
| `typst` | `typst` 0.15.0 on PATH; skipped without it | `typst` (Linux) |
| `process_table` | The process table: the PowerShell helper on Windows, `/proc` on Linux; skipped elsewhere | `python-tests-windows` |
| `render` | Real Manim renders, and LaTeX on PATH for formulas | `render`, in the Slow workflow |
| `export` | Like `render`: the 1920×1080, 30 fps export of the reference scene | `render`, in the Slow workflow |
| `offline` | A built `packaging/bundle`, run with the network blocked | `render`, in the Slow workflow |

The fast lane is everything without a slow marker:

```text
uv run pytest tests scripts ci -m "not render and not export and not offline and not typst and not process_table"
```

`uv run pytest` alone runs every test in `tests/`, the slow tiers included.

Two environment variables open the tests that touch the operating system:

- `MATHIMATOR_WIN32_TESTS=1` runs the Job Object tests, which create real Job
  Objects ([`tests/test_win32_job.py`](../../tests/test_win32_job.py) and the
  shell's ignored Job Object test). Without it,
  [`conftest.py`](../../tests/conftest.py) sets `MATHIMATOR_WIN32_JOBS=0` for
  every test, so the workers the tests start create no Job Object.
- `MATHIMATOR_REAL_WORKER_TESTS=1` runs the shell's ignored tests that start
  the repository's `.venv` Python, the real worker among them.

[CONTRIBUTING.md](../../CONTRIBUTING.md#environment-variables) lists every
`MATHIMATOR_*` variable, and
[`ci/test_project_docs.py`](../../ci/test_project_docs.py) checks that its
table names exactly the ones the code reads.

## How the Python tests are organised

The modules are grouped by what they test:

- **The document:** [`test_document_validation.py`](../../tests/test_document_validation.py)
  (every refusal and its code and field),
  [`test_characterization.py`](../../tests/test_characterization.py) (pinned
  cache identities and generated code, which must not change by accident) and
  [`test_legacy_goldens.py`](../../tests/test_legacy_goldens.py) (documents
  from before the beat catalog keep their bytes, code and keys).
- **Edits:** [`test_edit_ops.py`](../../tests/test_edit_ops.py) (every example
  of `edit-ops.json` as one undo step),
  [`test_edit_requests.py`](../../tests/test_edit_requests.py) (the same edits
  as `edit` requests through the supervisor) and
  [`test_beat_edits.py`](../../tests/test_beat_edits.py) (the beat and shape
  operations, their refusals and the repair of `delete_object`).
- **Beats, timeline and codegen:** [`test_beats.py`](../../tests/test_beats.py)
  (the catalog), [`test_timeline.py`](../../tests/test_timeline.py) (the
  on-screen rules), [`test_codegen_beats.py`](../../tests/test_codegen_beats.py)
  (the code of every kind, shape and easing) and
  [`test_smoke_projects.py`](../../tests/test_smoke_projects.py) (the
  render-smoke projects are current and compile).
- **The supervisor and the protocol:** [`test_protocol.py`](../../tests/test_protocol.py)
  (the envelope, replies, every refusal, cancel and the TeX state),
  [`test_shell_fixture.py`](../../tests/test_shell_fixture.py) (a worker
  started as the shell starts it answers `requests.json`),
  [`test_jobs.py`](../../tests/test_jobs.py) (jobs, progress and publishing),
  [`test_lifecycle.py`](../../tests/test_lifecycle.py) (the lock, fingerprint,
  folder identity, links and the project commands),
  [`test_project_files.py`](../../tests/test_project_files.py) (the layout, the
  preview index and exports, without a supervisor) and
  [`test_followup.py`](../../tests/test_followup.py).
- **Manim and processes:** [`test_manim_run.py`](../../tests/test_manim_run.py)
  (how Manim and LaTeX are run), [`test_cache_safety.py`](../../tests/test_cache_safety.py)
  (scene code and cache safety), [`test_process_control.py`](../../tests/test_process_control.py)
  (the fallback tree kill and the one-second cancel) and `test_win32_job.py`.
- **Acceptance tests (section 7 of [the spec](../design-spec.md)):** a module
  per area: the reference scenes and the supervisor, codegen, partial
  re-renders and invalidation, the equation preview, the templates and 3D
  scenes, and the export presets and notices. Most of their tests are fast and
  start no Manim. The timed renders are marked `render` or `export`, in the
  module itself or in a companion `*_render.py` module, and the offline bundle
  check is marked `offline`.
- **Renders:** [`test_beats_render.py`](../../tests/test_beats_render.py)
  previews every render-smoke project for real.

### Helpers

[`tests/support.py`](../../tests/support.py) holds what the modules share:

- `start_supervisor` and `SupervisorClient` start a real worker process and
  speak to it over stdin and stdout, as the shell does.
- `FakeManim` copies a stand-in `manim` package
  ([`tests/fixtures/fake_manim`](../../tests/fixtures/fake_manim)) onto
  `PYTHONPATH`. It follows a plan the test writes: which files to write, when
  to hold, what to log and how to exit. The job, progress and cancel tests run
  the real supervisor against it, with no real render.
- `complete_clip`, `write_clip` and `video_render` write minimal MP4 or WebM
  files that pass the clip check, for progress and index tests.
- `link_folder` and `link_file`, with the `link_kind` fixture in
  [`conftest.py`](../../tests/conftest.py), make symbolic links and junctions,
  so each link refusal is tested with every kind of link.
- `compile_csharp` builds the C# stand-in programs in
  [`tests/fixtures/win32`](../../tests/fixtures/win32) that the Job Object
  tests start.

[`tests/beat_documents.py`](../../tests/beat_documents.py) builds small
documents for the beat tests, and
[`tests/smoke_projects.py`](../../tests/smoke_projects.py) the render-smoke
projects.

### Goldens

`--update-golden` (from [`conftest.py`](../../tests/conftest.py)) makes these
tests rewrite their golden files instead of comparing with them:

| Test | Golden |
| --- | --- |
| `tests/test_shell_fixture.py` | `tests/fixtures/protocol/replies.json` |
| `tests/test_beats.py` | `tests/fixtures/protocol/beat-kinds.json` |
| `tests/test_codegen_beats.py` | `tests/fixtures/beats/codegen.json` |
| `tests/test_smoke_projects.py` | the projects in `tests/fixtures/render-smoke` |

Run the test with the flag, review the diff, and commit the golden with the
change that caused it. `legacy-goldens.json` and the scenes in
[`scenes`](../../scenes) are never rewritten by a flag.

## Shared fixtures

| Fixture | Used by |
| --- | --- |
| [`tests/fixtures/protocol`](../../tests/fixtures/protocol): `commands.json`, `requests.json`, `replies.json`, `edit-ops.json`, `export-choices.json`, `beat-kinds.json` | The worker, the shell and the UI ([the protocol page](protocol.md#the-fixtures) says which) |
| [`tests/fixtures/beats/canonical-order.json`](../../tests/fixtures/beats/canonical-order.json) | The worker's target order and the UI's copy |
| [`tests/fixtures/timing/frame-boundaries.json`](../../tests/fixtures/timing/frame-boundaries.json) | Codegen's `_beat_time` and the UI's scrub |
| [`tests/fixtures/equation/translate-cases.json`](../../tests/fixtures/equation/translate-cases.json) | The shell's translator and the worker's reference |
| [`tests/fixtures/render-smoke`](../../tests/fixtures/render-smoke) | A project for each beat kind and shape, and scenes for arrows, opacity, several targets, easings, re-entry and 3D. The worker previews them in the render tier, and the UI's state parser reads each one |
| `reference-60`, `lesson-60`, `journey-a`, `fractional-10` | Sample projects for the reference timings, invalidation and the frame rule; `reference-60` is also the bundled sample |
| [`tests/fixtures/manim`](../../tests/fixtures/manim) | Recorded Manim output and TeX logs for the failure messages |
| [`tests/fixtures/win32`](../../tests/fixtures/win32) | C# stand-ins for the Job Object tests |

## UI tests

vitest runs every `src/**/*.test.{ts,tsx}` file ([`vite.config.ts`](../../desktop/vite.config.ts)).
Tests run in Node by default. Component and hook tests opt into jsdom with a
`// @vitest-environment jsdom` comment, and mock `@tauri-apps/api` with
`vi.mock`, so no shell is needed.

The UI's tests import the shared fixtures directly: `commands.test.ts` checks
the command tables against `commands.json`, `protocolGolden.test.ts` parses
every line of `replies.json`, `edits.test.ts` checks each builder against
`edit-ops.json` and that no other source writes an edit by hand,
`beatKinds.test.ts` checks the catalog copy against `beat-kinds.json`, and
`playback.test.ts` checks the scrub against `frame-boundaries.json`.

```text
cd desktop
npm test               # once
npm run test:ci        # with coverage and a JUnit report, as CI runs it
npm run lint
npm run typecheck
npm run format:check
```

## Shell tests

The shell's tests are unit tests inside each module. They read the shared
fixtures with `include_str!`: [`lib.rs`](../../desktop/src-tauri/src/lib.rs)
checks the registered commands against `commands.json`,
[`protocol.rs`](../../desktop/src-tauri/src/protocol.rs) builds every line of
`requests.json` from its `Request` type,
[`worker.rs`](../../desktop/src-tauri/src/worker.rs) checks which request each
command sends, and [`equation.rs`](../../desktop/src-tauri/src/equation.rs)
runs the translator over `translate-cases.json`. The transport tests drive a
fake worker process through the `Spawner` closure type and the
`WorkerProcess` trait.

Three tests are `#[ignore]`d because they need the real system: the Job Object
test (`MATHIMATOR_WIN32_TESTS=1`) and two that start the repository's Python
(`MATHIMATOR_REAL_WORKER_TESTS=1`). Run them with
`cargo test --locked -- --include-ignored`. Every cargo command needs
`npm run build` first, because the build embeds `desktop/dist`.

## Lint, types and the CI checks

- **Python:** `uv run ruff check`, `uv run ruff format --check`, and mypy in
  strict mode twice, once as Windows (`uv run mypy`) and once as Linux
  (`uv run mypy --platform linux`), so both sides of every platform branch are
  type-checked on any machine. mypy checks the worker and `scripts/`.
- **TypeScript:** ESLint, `tsc --noEmit` and Prettier.
- **Rust:** `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings`.
- **The repository itself:** [`ci/test_pipeline.py`](../../ci/test_pipeline.py)
  holds the workflows to the scripts, pins and paths they name, and the CI
  lanes to the files their jobs read.
  [`ci/test_project_docs.py`](../../ci/test_project_docs.py) checks the
  environment variable table in CONTRIBUTING.md, the npm scripts the documents
  name and the code owners file. The scripts in `scripts/` have their tests
  beside them.

The pre-commit hooks in [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)
run the same lint checks locally, plus whitespace, line-ending, YAML, TOML and
JSON checks, the schema checks of the workflows and the Dependabot
configuration, and gitleaks on the staged changes:

```text
uv run pre-commit install
uv run pre-commit run --all-files
```

## CI

GitHub Actions runs four workflows on GitHub-hosted `ubuntu-24.04` and
`windows-2025` runners:

| Workflow | Runs on | Does |
| --- | --- | --- |
| [`ci.yml`](../../.github/workflows/ci.yml) | Every pull request, every push to `main`, and every `v*` tag through the release | Lint, types, the fast tests, the UI and shell suites, and the dependency audits |
| [`slow.yml`](../../.github/workflows/slow.yml) | Every night on `main`, by hand from the Actions tab, and every `v*` tag through the release | The `render`, `export` and `offline` tiers on Windows against a freshly built bundle, and the installer build |
| [`release.yml`](../../.github/workflows/release.yml) | A pushed `v*` tag | The tag check, CI, Slow, release notes and the draft release ([packaging and release](packaging-and-release.md#the-release-flow)) |
| [`codeql.yml`](../../.github/workflows/codeql.yml) | Every pull request, every push to `main`, and weekly | CodeQL scanning of the Python, TypeScript, Rust and workflow code |

The jobs of `ci.yml`:

| Job | Runner | Runs |
| --- | --- | --- |
| `changes` | Linux | `ci/changed_lanes.py`, which picks the lanes a pull request needs |
| `python-lint` | Linux | `check_versions.py`, Ruff, mypy for Windows and Linux, `uv lock --check`, the worker package build and the `ci/` tests |
| `hygiene` | Linux | The pre-commit hooks no other job runs |
| `ui` | Linux | ESLint, tsc, Prettier, vitest with coverage, and the production build |
| `rust-linux` | Linux | rustfmt, Clippy and cargo test |
| `rust-windows` | Windows | rustfmt, Clippy and cargo test with the ignored tests, which compiles the shell's Windows-only code |
| `python-tests` | Linux | The fast lane on CPython 3.14.9 and 3.13.17, with the worker's coverage |
| `python-tests-windows` | Windows | The fast lane with the Win32 and process-table tests, and the `ci/` tests |
| `typst` | Linux | The `typst` tier, with the pinned Typst from `ci/install-typst.sh` |
| `audit-python`, `audit-npm`, `audit-rust` | Linux | pip-audit of `uv.lock`, npm audit of the UI's runtime packages, and cargo-deny with `ci/deny.toml` |
| `result` | Linux | `CI result`, the one required check: it passes when every other job passed or was skipped |

`changes`, `python-lint`, `hygiene` and `result` run every time. Every other
job belongs to a lane, and in a pull request a lane runs only when the change
touches a file its jobs test, lint or read, as
[`ci/changed_lanes.py`](../../ci/changed_lanes.py) defines. A push to `main`,
a tag, or a change to `ci.yml` or `changed_lanes.py` runs every lane.

The jobs upload their JUnit reports and the worker's and the UI's coverage as
artifacts. Coverage is reported, never enforced.

Pull requests never run the slow tiers. A change to rendering, export, the
bundle or its pins should be checked by starting the Slow workflow on its
branch from the Actions tab.
