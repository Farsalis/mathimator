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
  typed refusals and failures, and job events (`queued`, `started`,
  `rejected`, `worker_exited`) that end every job.
- shell: worker calls run off the UI thread behind one ordered request gate,
  a crashed worker is respawned only after it has exited, and new commands
  cover New, Save As, Reload, Overwrite and a Re-export that bypasses the
  export cache.
- ui: an ordered worker queue, parsers that name the failing field, automatic
  previews after Open, Sample and Clear cache, and Open and Sample disabled
  during an export.

### Fixes

- Scene code and the render cache are hardened: the codegen folder is
  recreated before each render, the project path is resolved before it
  reaches Manim or TeX, unfinished media is discarded after a failed or
  interrupted run, and every partial clip is checked for completeness before
  it is cached or published.

### Documentation

- The architecture, defect and DevOps audit report and its fix plan.
- README, CONTRIBUTING and SECURITY: setup, running, test tiers, the
  installer build, CI, the environment variables, commit and merge rules,
  releases, and reporting vulnerabilities in confidential issues.
- GitLab code owners for every area, a merge request checklist, and bug and
  feature issue templates.

### Tests

- render, export, offline, typst and process_table markers, enforced with
  `--strict-markers`.
- Shared fixtures for the LaTeX-to-Typst translator, the protocol commands and
  the Win32 C# stand-ins.

### Build

- One hashed uv.lock for the dev tools and the Manim render stack, synced with
  uv 0.13.5; the worker supports Python 3.13 and later.
- Pinned toolchains: Rust 1.93.0 with rustfmt and clippy, Node 24 and
  CPython 3.14.9.
- Ruff, mypy --strict, type-aware ESLint, Prettier and rustfmt, pre-commit
  hooks that mirror CI, and one formatting commit hidden from git blame.
- The installer bundle ships a relocatable CPython, installs the render stack
  from uv.lock with hashes, and checks every download against a pinned
  SHA-256.
- scripts/check_bundle.py checks a built bundle before `tauri build`,
  scripts/generate_notices.py writes its third-party notices, including the
  GPL x264 and x265 encoders and a source offer, and scripts/check_versions.py
  checks the app version and toolchain pins. bump-my-version, git-cliff and
  pip-audit join the dev tools.

### CI

- A GitLab CI/CD pipeline for gitlab.com. Hosted Linux runners lint, run the
  fast pytest lane on Python 3.13 and 3.14, the Typst tests, vitest and the
  Rust pre-checks, and report JUnit results and worker coverage. The hosted
  Windows runner runs the Win32 tests, Clippy and cargo test, a nightly render
  lane, and builds the installer on tags, nightly and on demand.
- SAST, Secret Detection, pip-audit, npm audit and cargo-deny; a protected-tag
  release that uploads the installer to the package registry and creates a
  GitLab Release; Renovate with the spec's pins.

### Chores

- LF line endings, editor settings and ignore rules.
