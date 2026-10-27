# Mathimator developer documentation

These pages explain how Mathimator works, for anyone who wants to read or
change its code. They describe the code as it is today.
[design-spec.md](design-spec.md) is the specification the code is held to: it
states the rules, the messages and the acceptance criteria, and where a page
here and the spec disagree, the spec is right.

Start with the overview, then read the page for the part you are changing.

| Page | What it covers |
| --- | --- |
| [Architecture overview](architecture/overview.md) | The three processes, how they talk, who owns which state, and how `project.json` becomes a rendered movie |
| [The worker](architecture/worker.md) | The Python supervisor: binding a project, sessions and undo, the document model and validation, the beat catalog and the on-screen timeline, the job queue, progress, exports, and process control on Windows and Linux |
| [The shell](architecture/shell.md) | The Tauri app in Rust: its commands, the worker transport, runtime resolution, temp projects and the project lock, the TeX self-test, the glyph preview and the logs |
| [The UI](architecture/ui.md) | The React app: state and hooks, the worker queue, the job session, playback and scrub, the Inspector and the beat editor, + Beat and shapes |
| [The protocol](architecture/protocol.md) | A tour of the JSON-lines protocol between the shell and the worker, and its fixtures |
| [Rendering and caching](architecture/rendering-and-caching.md) | Scene generation, the frame rule, partial movies, cache identity, clip validation, exports and re-export |
| [Packaging and release](architecture/packaging-and-release.md) | The runtime bundle and its pins, the bundle check, the third-party notices, the installer, versions and the GitHub release flow |
| [Testing](architecture/testing.md) | Test tiers and markers, shared fixtures, how to run each suite, and the CI jobs |

The repository's other documents:

- [README.md](../README.md): what Mathimator is, and how to build, run and
  test it.
- [CONTRIBUTING.md](../CONTRIBUTING.md): the development setup, environment
  variables, the rules a change follows, commits, pull requests and releases.
- [SECURITY.md](../SECURITY.md): how to report a vulnerability, and what is in
  scope.
- [CHANGELOG.md](../CHANGELOG.md): the changes in each release.

## Where a change goes

Some changes cross the three processes. These are the places each one touches.

| Change | Where |
| --- | --- |
| A new edit operation, or new fields of one | The edit function and its `_EDITS` entry in [`session.py`](../worker/mathimator_worker/session.py); the `Edit` type in [`types.ts`](../desktop/src/types.ts) and a builder in [`edits.ts`](../desktop/src/edits.ts); an example in [`edit-ops.json`](../tests/fixtures/protocol/edit-ops.json); section 6 of the spec |
| A new worker command or event | Both ends of [the protocol](architecture/protocol.md#changing-the-protocol), its fixtures, and section 6 of the spec |
| A new Tauri command | The command and its `commands!` entry in [`lib.rs`](../desktop/src-tauri/src/lib.rs), the tables in [`commands.ts`](../desktop/src/commands.ts), and [`commands.json`](../tests/fixtures/protocol/commands.json) |
| A new beat kind, easing or shape | Its row in [`beats.py`](../worker/mathimator_worker/beats.py), and in [`codegen.py`](../worker/mathimator_worker/codegen.py) if its code takes a new form; the UI's copy in [`beatKinds.ts`](../desktop/src/beatKinds.ts); the goldens `beat-kinds.json`, `codegen.json` and the render-smoke projects (see [Testing](architecture/testing.md#goldens)); sections 4 and 5 of the spec |
| Different generated code for an existing document | [`codegen.py`](../worker/mathimator_worker/codegen.py), a `GENERATOR_VERSION` bump in [`document.py`](../worker/mathimator_worker/document.py), the goldens in [`scenes`](../scenes) and `codegen.json`, and section 5 of the spec ([why](architecture/rendering-and-caching.md#when-to-bump-generator_version)) |
| An export format, size or frame rate, or a beat or timeline limit | `FORMATS`, `EXPORT_SIZES`, `EXPORT_FPS` or the limits in [`document.py`](../worker/mathimator_worker/document.py); [`exportProfile.ts`](../desktop/src/exportProfile.ts) or [`format.ts`](../desktop/src/format.ts); [`export-choices.json`](../tests/fixtures/protocol/export-choices.json); the spec |
| A pinned tool version | Every copy at once: [`packaging/versions.env`](../packaging/versions.env) and the files [`check_versions.py`](../scripts/check_versions.py) compares it with ([Versions](architecture/packaging-and-release.md#versions)) |

## Words used in these pages

| Word | Meaning |
| --- | --- |
| **Project** | A folder holding `project.json` and the derived `.mathimator/` folder |
| **Document** | The content of `project.json`: the scene's objects, its beats, the template instances and the two profiles |
| **Object** | Something drawn: axes, a plot, a formula, a text, a shape, or the 3D template's axes and surface or curve |
| **Beat** | One step on the timeline: one kind of animation on its targets, for its duration. It compiles to exactly one `self.play` or `self.wait` |
| **Kind** and **family** | A beat's animation, such as `fade_in`, and its family, such as entrance, which says what it does to its targets' on-screen state |
| **Template instance** | A recorded insertion of a template, whose params are the source of truth for the objects it created |
| **Partial movie** | The clip Manim writes for one `play` or `wait`, which it reuses while the call is unchanged |
| **Preview profile**, **export profile** | The fixed 854×480, 15 fps preview, and the format, size, frame rate and transparency of an export |
| **Job** | A preview or an export, with an id such as `preview-7`; at most one runs Manim |
| **Binding** | The worker's hold on one project folder: its lock, its identity and the fingerprint of `project.json` |
| **Dirty**, **fresh** | A beat is dirty when its key differs from the last full preview, so it will render again. A preview file is fresh when it was rendered for the document as it is now |
| **Last good** | The previous clip of a beat that is out of date, shown under the "Preview is out of date" badge until its new clip is ready |
| **Temp project** | A project the shell made in `%TEMP%` for a launch, New or Sample, which it offers to save before leaving it |
| **Runtime bundle** | `packaging/bundle`: the Python, TeX and Typst that the installer ships, so the app needs nothing installed beside it |
| **Supervisor** | Another name for the worker process, which supervises the Manim processes |
