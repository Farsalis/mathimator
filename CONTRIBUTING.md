# Contributing to Mathimator

This guide covers the development setup, the rules a change follows, commits,
merge requests and releases. [README.md](README.md) explains what Mathimator is
and how to build, run and test it. [docs/design-spec.md](docs/design-spec.md)
is the specification every change is held to.

## Development setup

Follow the quick start in the README, then install the git hooks and the blame
setting once per clone:

```powershell
uv run pre-commit install
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

### Git hooks

The hooks in `.pre-commit-config.yaml` mirror the CI lint jobs: whitespace,
line-ending, YAML, TOML and JSON checks, Ruff lint and format, mypy --strict
for the win32 and linux platforms, gitleaks on the staged changes, and
Prettier, ESLint, tsc and cargo fmt for `desktop/`. The desktop hooks run the
project's own tools, so they need `npm ci` in `desktop/` and the pinned Rust
toolchain. The first run downloads the hook environments. To check the whole
tree:

```powershell
uv run pre-commit run --all-files
```

### Formatting commits and git blame

`.git-blame-ignore-revs` lists commits that only reformat code, so that
`git blame` skips them once `blame.ignoreRevsFile` points at it. If you make
another formatting-only commit, add its full hash to the file in a follow-up
commit.

### Linux

Linux runs the fast tests, the Typst tests, vitest and the Rust pre-checks, but
not the app's Windows code. The render group builds pycairo and ManimPango
from source on Linux, so sync without it unless the Cairo and Pango
development packages are installed:

```bash
uv sync --locked --no-group render
```

The Rust pre-checks also need WebKitGTK 4.1 and libxdo, which Debian and
Ubuntu package as `libwebkit2gtk-4.1-dev` and `libxdo-dev`.

## Environment variables

| Variable | Read by | Effect |
| --- | --- | --- |
| `MATHIMATOR_WIN32_TESTS` | pytest, cargo test | `1` runs the Job Object tests, which create real Job Objects: `tests/test_win32_job.py`, and the shell's Job Object test under `cargo test -- --include-ignored`. Without it, the pytest suite sets `MATHIMATOR_WIN32_JOBS=0`, so the workers it starts create no Job Object. |
| `MATHIMATOR_REAL_WORKER_TESTS` | cargo test | `1` runs the shell's ignored tests that start the repository's `.venv` Python under `cargo test -- --include-ignored`: the real worker's handshake and project lock, and the check that a debug shell spawns the interpreter behind the venv launcher. Without it those tests return at once. |
| `MATHIMATOR_WIN32_JOBS` | worker, debug shell | `0` turns the kill-on-close Job Objects off. The worker then cancels a render by walking its process tree, and a debug shell leaves the worker outside a job. Unset means on. Release shells ignore it. |
| `MATHIMATOR_SKIP_PROCESS_TABLE` | worker | `1` keeps the worker from starting the Windows process-table helper, a PowerShell child, ahead of a cancel. A cancel that walks the process tree still starts it. Tests that start a worker but never cancel set it. |
| `MATHIMATOR_PYTHON` | debug shell, tests | The interpreter that runs the worker, in place of `.venv`. |
| `MATHIMATOR_TYPST` | debug shell | The `typst` executable for the glyph preview, in place of the bundle's or the one on PATH. |
| `MATHIMATOR_TINYTEX_BIN` | debug shell | The TinyTeX `bin` folder, in place of the bundle's. |
| `MATHIMATOR_SHELL_READY_FILE` | shell | When it names a file, the shell writes `ready <unix ms>` there once the UI is up. The startup timing measurements read it. It is the only one of these variables that a release shell reads. |
| `MATHIMATOR_TEX_ERROR` | worker | Set by the shell, not by hand, to the reason formulas cannot render. The worker then refuses jobs that need LaTeX with `tex_unavailable` and that reason, until the shell's `set_tex_error` request replaces it. |
| `MATHIMATOR_TEST_*` | Win32 stand-ins | Set by the Job Object tests for the C# stand-in programs in `tests/fixtures/win32`. |

The three tool overrides, `MATHIMATOR_PYTHON`, `MATHIMATOR_TYPST` and
`MATHIMATOR_TINYTEX_BIN`, apply to debug builds only. A release shell uses only
its installed runtime and notes any override it ignored in `shell.log`. A
Typst or TinyTeX override that points at nothing disables only the glyph
preview or the formulas.

## Making a change

- Open an issue first for anything larger than a fix, and for any new
  dependency: every runtime dependency ships in the installer and its notices.
- Behaviour follows `docs/design-spec.md`. A change of behaviour updates the
  spec in the same merge request.
- Every fix comes with a test. Run the fast lane and the tiers your change
  touches; the README lists them.
- The worker imports only the standard library, and the tests check that it
  never imports Manim, PyAV or pywin32.
- A change to the generated scene code bumps `GENERATOR_VERSION` in
  `worker/mathimator_worker/document.py`, which is part of every cache key, and
  updates the golden code in `scenes/` and spec section 5.
- A change to the commands or events between the shell and the worker updates
  both sides, `tests/fixtures/protocol/commands.json`, which the Python, Rust
  and TypeScript tests check, `requests.json` beside it, which the Python and
  Rust tests check, and spec section 6. It also rewrites `replies.json`, the
  lines the worker writes for `requests.json`, which the TypeScript tests read:
  `uv run pytest tests/test_shell_fixture.py --update-golden`. An incompatible
  change also bumps the protocol version in the worker and in the shell.
- The pinned versions change only by hand and in every copy at once: Manim
  0.22.0, Typst 0.15.0, CPython 3.14.9 with 3.13.17 as the fallback, uv
  0.13.5, Rust 1.93.0, Node 24, Tauri 2.12.x, React 19 and Vite 8.
  `scripts/check_versions.py` fails CI when the copies disagree. A change to a
  version the spec names is also a spec revision. Renovate proposes none of
  these changes.
- Only the tools write the lockfiles: `uv lock` for `uv.lock`, npm for
  `desktop/package-lock.json`, and cargo for `desktop/src-tauri/Cargo.lock`.

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).
git-cliff turns them into the changelog (`cliff.toml`), so the subject line is
what a release lists. Write it in the imperative, and in the body say why and
name the issue, audit finding or decision it addresses.

| Type | Changelog section |
| --- | --- |
| `feat` | Features |
| `fix` | Fixes |
| `perf` | Performance |
| `refactor` | Refactoring |
| `docs` | Documentation |
| `test` | Tests |
| `build` | Build |
| `ci` | CI |
| `chore` | Chores |
| `revert` | Reverts |
| `style` | Left out: formatting only |

The scopes in use are `worker`, `ui` and `shell`, for example
`fix(worker): refuse a newer generatorVersion`. Mark a breaking change with `!`
after the type or scope, or with a `BREAKING CHANGE:` footer.

Make one commit per verified step: each commit builds and passes its tests.
Commit messages carry no AI attribution trailers, such as `Co-Authored-By`
lines naming a coding assistant.

## Branches and merge requests

- `main` is protected. Nobody pushes to it; changes reach it only through
  merge requests.
- Work on a topic branch and open a merge request against `main`. The default
  template holds the review checklist.
- Merges use merge commits with semi-linear history: rebase the branch onto
  `main` when GitLab asks, and merge once the pipeline passes. Squashing is
  off, so each verified commit stays in the history.
- `.gitlab/CODEOWNERS` names the owner of each area.

## Releases

A release is a merge request and a protected tag.

1. On a release branch, bump the version in every manifest and regenerate the
   changelog, then commit both:

   ```powershell
   uv run bump-my-version bump minor   # or major, or patch
   uv run git-cliff --tag vX.Y.Z --output CHANGELOG.md
   git commit -am "chore(release): vX.Y.Z"
   ```

2. Merge the release merge request into `main`.
3. A maintainer tags the merge commit `vX.Y.Z` and pushes the tag to GitLab.
4. The tag pipeline runs every lane, the render lane included. `lint:versions`
   fails unless the tag is `v` followed by the app version, and
   `package:windows` builds the installer.
5. Once every job passes, the release jobs upload the installer,
   `THIRD_PARTY_NOTICES.txt` and `SHA256SUMS` to the project's generic package
   registry, and create a GitLab Release that links them, with notes git-cliff
   writes from the commits since the previous tag.

v1 installers are unsigned. Signing needs no code change: setting the protected
CI/CD variable `WINDOWS_SIGN_COMMAND` makes `ci/package-windows.ps1` sign the
app and the installer (the script describes the format). The installer ships
GPL-licensed encoders inside PyAV's FFmpeg; its notices get a legal review
before the first public release.

## GitLab project settings

The pipeline and these rules expect the GitLab project to have:

- `main` as a protected branch that allows no pushes and merges by
  maintainers, and `v*` as protected tags that maintainers create;
- merge method "Merge commit with semi-linear history", squashing not
  allowed, and merges only after a successful pipeline;
- the gitlab.com instance runners, the Linux ones and the hosted Windows
  runner that the `saas-windows-medium-amd64` tag selects;
- two pipeline schedules on `main`: a nightly one, and one that sets the
  variable `SCHEDULE_KIND=renovate`;
- the masked CI/CD variables that `ci/renovate.yml` describes,
  `RENOVATE_TOKEN` and `GITHUB_COM_TOKEN`.
