# Changelog

Notable changes to Mathimator, by release. git-cliff generates this file from
the Conventional Commits history (cliff.toml), so write the entry in the commit
message rather than here.

## [Unreleased]

This summary stands in until the first release, whose changelog git-cliff
generates from the commits.

### Features

- Protocol 2 between the shell, the UI and the worker: a versioned handshake,
  typed refusals and failures with codes, and jobs that end with exactly one
  `done`, `error` or `rejected` line.
- worker: an exclusive lock on the open project, atomic saves, and a
  fingerprint of project.json that catches edits made outside the app, which
  the user can reload or overwrite. `open_project` takes `init`, which New
  uses to start an empty project, and Reload, Overwrite and Save As are new
  commands. A held preview or export waits for the job before it, and a
  Re-export bypasses the export cache.
- worker: `set_tex_error` replaces the TeX error of the running worker, so a
  failed TeX self-test no longer stops a running export or clears undo.
- shell: worker calls run off the UI thread behind one ordered request gate,
  a crashed worker is respawned only after it has exited, and a worker that
  cannot start is shown as unavailable, with its reason and Retry.
- shell: the last project reopens at launch, with a notice when it is
  missing. Temp projects are marked, an untouched one is pruned after 24
  hours, and leaving a touched one offers Save As. A window close ignored
  while a dialog runs gets a notice.
- shell: a TeX self-test after every launch. While TeX fails, the worker
  refuses renders that need LaTeX with `tex_unavailable`.
- shell: the LaTeX-to-Typst glyph translator runs in Rust, and Typst
  compiles off the UI thread behind a gate per formula.
- shell: the permanent identifier com.mathimator.desktop, which renames the
  app-data and log folders. The webview is granted `core:default` only, under
  a content security policy.
- ui: an ordered worker queue, parsers that name the failing field, automatic
  previews after Open, Sample and Clear cache, a prompt for a project changed
  on disk, an error banner with details, New and Save As buttons, a Worker
  unavailable view with its reason and Retry, and Open and Sample disabled
  during an export.
- ui: Save As, including one from the leave warning, keeps the selection,
  playhead, open panel and note; Clear cache keeps the shown export; a
  preview that used an incomplete clip offers Render again.
- The Notices panel shows the shipped THIRD_PARTY_NOTICES.txt below a
  corrected summary, and the notices name the embedded WebView2 installer.
- A beat catalog of 28 kinds in seven families, each still one `play` or
  `wait`: wait; the entrances create, fade in, write, grow from center, grow
  arrow, draw border then fill and spin in; the exits fade out, uncreate,
  unwrite and shrink away; the changes move by, move to, scale, rotate, set
  opacity, recolor and move along a path; the emphasis indicate,
  circumscribe, flash, wiggle, focus and wave; the morphs transform and the
  formula transform that keeps matching symbols; and a camera move in 3D
  scenes. A beat may act on several objects at once, every animated beat
  has an easing from nine of Manim's rate functions, and an object may leave
  the screen and enter again: validation plays the timeline out and tracks
  what is on screen. Documents without these keep their bytes, generated
  code and cache keys.
- Shapes: Dot, Line, Arrow, Circle, Rectangle and Point on axes, added from
  Add with their entrance beat, each with position, size and colour fields.
- worker: the edit ops add_beat, set_beat_action, set_beat_targets,
  set_beat_param, set_beat_easing, delete_beat, move_beat, add_shape and
  set_shape_param, with set_beat_color and set_beat_shift kept as aliases.
  The state reply lists what is on screen before each beat, and deleting an
  object repairs the beats it leaves behind.
- ui: + Beat on the timeline, a beat editor in the Inspector for the kind,
  targets, settings, easing and order of a beat and for deleting it, a
  selection that holds a beat and an object at once, timeline labels that
  name the targets, and the shape fields.

### Fixes

- worker: -x^2 is -(x^2), expression literals are emitted exactly, formula
  LaTeX is limited to an allowlist, and beat boundaries round to the nearest
  frame from whole milliseconds, as the UI rounds them (generator version 2).
- worker: limits on document size, text, numbers, axis ticks and expression
  depth, with every refusal naming its field; an edit of a field a template
  owns is refused as `invalid_edit`.
- Scene code and the render cache are hardened: Manim runs from a fresh
  codegen folder that is also its home, a project path Manim or TeX cannot
  use is refused, TeX runs with shell escape off, partial clips are checked
  for completeness before they are cached or published, and what a
  cancelled, failed or interrupted run wrote is discarded by the snapshot in
  its run marker.
- worker: a PNG-sequence export writes one PNG per frame, waits included, and
  its frame count is checked against the document.
- worker and shell: a project whose working folders are symbolic links or
  junctions is refused, no delete follows a link, and Clear cache and the
  codegen reset remove read-only files.
- worker: a LaTeX error shows TeX's `<argument>` line, which names the bad
  command.
- Windows cancel: one owner closes each Job Object handle, the fallback tree
  kill checks process creation times, and the real interpreter behind a venv
  launcher runs both Manim and a debug shell's worker, so neither escapes its
  Job Object.
- shell: a release build passes no inherited `MATHIMATOR_*` variable to the
  worker or the TeX self-test, and names each one it ignored in shell.log.
  Each worker process is ended once.
- worker: a preview rendered again with no edit reuses every clip. Manim
  runs with `PYTHONHASHSEED=0`, so a rotate or a wave beat has the same cache
  key in every Manim process, and a camera move resets the camera's rotation
  after its play, so the beats after it keep their keys. While Manim runs,
  the beats after the first camera move that are not fresh take their clips
  from the segment list.
- tokio 1.54.1 clears RUSTSEC-2026-0384.

### Refactoring

- worker: cache identity, the project layout, exports and the preview index
  in modules of their own, and an edit dispatch table. The scrub command and
  the equation command line are gone.
- ui: typed command tables, and focused hooks split out of useEditor.

### Documentation

- design-spec.md section 6 rewritten as the protocol 2 contract, and the rest
  of the spec brought up to date with the code.
- design-spec.md: the beat catalog, the shapes, the on-screen rules, easing
  and the camera move in section 4, the code of every kind in section 5, the
  beat and shape edits, + Beat and the beat editor in section 6, and their
  acceptance criteria and tests in section 7.
- README, CONTRIBUTING and SECURITY: setup, Linux notes, running, test tiers,
  the installer build, CI, the environment variables, commit and pull request
  rules, releases, the repository settings, and reporting vulnerabilities
  through GitHub's private vulnerability reporting.
- Code owners for every area, a pull request checklist, and bug and feature
  issue templates.

### Tests

- render, export, offline, typst and process_table markers, enforced with
  `--strict-markers`.
- Shared fixtures for the LaTeX-to-Typst translator, the protocol commands,
  requests and golden replies, the export choices, the frame boundaries and
  the Win32 C# stand-ins, and a fake Manim package.
- Every protocol refusal is covered, the worker is replayed on the debug
  shell's spawn line, and the UI parses its golden replies.
- Shell tests that start the real worker and the interpreter behind the venv
  launcher, run with `MATHIMATOR_REAL_WORKER_TESTS=1`.
- A fractional-beat fixture in the slow frame-count checks, and a 101-beat
  preview that stays fresh.
- Shared fixtures for the beat catalog (beat-kinds.json), every edit op with
  an accepted example (edit-ops.json) and the stored order of a beat's
  targets; codegen goldens for every kind; legacy goldens that hold
  documents written before the catalog to their bytes, code and keys; and
  quick render-smoke projects for every kind, shape and easing, which
  tests/test_beats_render.py previews with one clip per beat, rendering the
  cache scenes three times to check that Manim reuses every clip.
- Every example of edit-ops.json also goes to a supervisor as an `edit`
  request and reads back from `get_state`, undo, redo and a reload from
  disk; the UI's components send every edit through the builders those
  examples test, and the UI's parser reads every render-smoke document
  whole.

### Build

- One hashed uv.lock for the dev tools and the Manim render stack, synced with
  uv 0.13.5; the worker supports Python 3.13 and later.
- Pinned toolchains: Rust 1.93.0 with rustfmt and clippy, Node 24 and
  CPython 3.14.9.
- Ruff, mypy --strict, type-aware ESLint, Prettier and rustfmt, pre-commit
  hooks that mirror CI, and a git blame ignore list for formatting-only
  commits.
- The installer bundle ships a relocatable CPython, installs the render stack
  from uv.lock with hashes, checks every download, the Linux ones included,
  against a pinned SHA-256, and precompiles its Python and the worker to
  checked-hash bytecode.
- scripts/check_bundle.py checks a built bundle, scripts/generate_notices.py
  writes its third-party notices, including the GPL x264 and x265 encoders
  and a source offer, and on Windows `tauri build` runs both before it
  bundles. scripts/check_versions.py checks the app version and toolchain
  pins. bump-my-version, git-cliff and pip-audit join the dev tools.
- UI coverage with @vitest/coverage-v8, run by `npm run test:ci`.

### CI

- GitHub Actions CI for every pull request and every push to main: a pull
  request runs the lint checks and only the lanes its changes touch, and a
  push to main runs them all. Hosted Linux runners lint, run the fast pytest
  lane on Python 3.13 and 3.14, the Typst tests, vitest and the Rust checks,
  and upload JUnit results and worker and UI coverage. Hosted Windows runners
  run the Win32 tests, and Clippy and cargo test with the shell's Windows
  code.
- A Slow workflow that runs the render, export and offline suites and builds
  the installer every night, by hand and for every release tag.
- A release workflow: a `v*` tag that matches the app version runs CI and the
  Slow workflow and creates a draft GitHub Release with the installer,
  SHA256SUMS, the third-party notices and git-cliff notes.
- CodeQL code scanning, pip-audit, npm audit and cargo-deny, Dependabot with
  the spec's pins left alone, and schema checks of the workflows and the
  Dependabot configuration in pre-commit.
- The UI lane also runs for the beat and render-smoke fixtures that its tests
  read, and the lane check follows the UI's glob imports.

### Chores

- LF line endings, editor settings and ignore rules.
