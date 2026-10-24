# Changelog

Notable changes to Mathimator, by release. git-cliff generates this file from
the Conventional Commits history (cliff.toml), so write the entry in the commit
message rather than here.

## [Unreleased]

The audit/cleanup-gitlab-devops branch works through the architecture, defect
and DevOps audit in docs/architecture-audit-report.md and prepares the project
for GitLab CI. This summary stands in until the first release, whose changelog
git-cliff generates from the commits.

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
- tokio 1.54.1 clears RUSTSEC-2026-0384.

### Refactoring

- worker: cache identity, the project layout, exports and the preview index
  in modules of their own, and an edit dispatch table. The scrub command and
  the equation command line are gone.
- ui: typed command tables, and focused hooks split out of useEditor.

### Documentation

- The architecture, defect and DevOps audit report, its fix plan, and the
  remediation status of each finding.
- design-spec.md section 6 rewritten as the protocol 2 contract, and the rest
  of the spec brought up to date with the code and the owner's decisions.
- README, CONTRIBUTING and SECURITY: setup, Linux notes, running, test tiers,
  the installer build, CI and its compute-minutes policy, the environment
  variables, commit and merge rules, releases, setting up the GitLab project,
  and reporting vulnerabilities in confidential issues.
- GitLab code owners for every area, a merge request checklist, and bug and
  feature issue templates.
- The phase reports and the Windows audit report point to the spec and the
  audit's remediation status.

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

### Build

- One hashed uv.lock for the dev tools and the Manim render stack, synced with
  uv 0.13.5; the worker supports Python 3.13 and later.
- Pinned toolchains: Rust 1.93.0 with rustfmt and clippy, Node 24 and
  CPython 3.14.9.
- Ruff, mypy --strict, type-aware ESLint, Prettier and rustfmt, pre-commit
  hooks that mirror CI, and one formatting commit hidden from git blame.
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

- A GitLab CI/CD pipeline for gitlab.com, sized for the Free tier's compute
  minutes: a merge request runs only the lanes its changes touch, and there
  is no nightly pipeline. Hosted Linux runners lint, run the fast pytest lane
  on Python 3.13 and 3.14, the Typst tests, vitest and the Rust pre-checks,
  and report JUnit results and worker and UI coverage. The hosted Windows
  runner runs the Win32 tests, and on tags or on demand Clippy and cargo
  test, the render lane and the installer build.
- SAST, Secret Detection, pip-audit, npm audit and cargo-deny; a protected-tag
  release that uploads the installer to the package registry and creates a
  GitLab Release with glab; Renovate with the spec's pins.

### Chores

- LF line endings, editor settings and ignore rules.
