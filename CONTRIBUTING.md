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
from source on Linux, which needs the Cairo and Pango development packages.
Without them, set `UV_NO_GROUP=render`, as CI does: `pyproject.toml` makes
render a default group, which every `uv sync` and every `uv run` would install
again, and the variable keeps it out of all of them. Keep the export in your
shell profile:

```bash
export UV_NO_GROUP=render
uv sync --locked
```

The Typst tests need the pinned Typst on PATH. `ci/install-typst.sh` downloads
it, checks it against its SHA-256 in `packaging/versions.env` and installs it
into the folder you name, as `test:typst` does. It needs curl, sha256sum, tar
and xz:

```bash
bash ci/install-typst.sh .typst
export PATH="$PWD/.typst:$PATH"
uv run pytest tests -m typst
```

The Rust pre-checks also need WebKitGTK 4.1 and libxdo, which Debian and
Ubuntu package as `libwebkit2gtk-4.1-dev` and `libxdo-dev`.

## Environment variables

| Variable | Read by | Effect |
| --- | --- | --- |
| `MATHIMATOR_WIN32_TESTS` | pytest, cargo test | `1` runs the Job Object tests, which create real Job Objects: `tests/test_win32_job.py`, and the shell's Job Object test under `cargo test -- --include-ignored`. Without it, the pytest suite sets `MATHIMATOR_WIN32_JOBS=0`, so the workers it starts create no Job Object. |
| `MATHIMATOR_REAL_WORKER_TESTS` | cargo test | `1` runs the shell's ignored tests that start the repository's `.venv` Python under `cargo test -- --include-ignored`: the real worker's handshake and project lock, and the check that a debug shell spawns the interpreter behind the venv launcher. Without it those tests return at once. `test:rust:windows` sets it after `uv venv` makes a bare `.venv`, since the worker needs only the standard library. |
| `MATHIMATOR_WIN32_JOBS` | worker, debug shell | `0` turns the kill-on-close Job Objects off. The worker then cancels a render by walking its process tree, and a debug shell leaves the worker outside a job. Unset means on. Release shells neither read it nor pass it to the worker. |
| `MATHIMATOR_SKIP_PROCESS_TABLE` | worker | `1` keeps the worker from ever starting the Windows process-table helper, a PowerShell child, so a cancel without a Job Object ends Manim alone, not its TeX children. Tests that start a worker but never cancel set it. Release shells do not pass it to the worker. |
| `MATHIMATOR_PYTHON` | debug shell, tests | The interpreter that runs the worker, in place of `.venv`. |
| `MATHIMATOR_TYPST` | debug shell | The `typst` executable for the glyph preview, in place of the bundle's or the one on PATH. |
| `MATHIMATOR_TINYTEX_BIN` | debug shell | The TinyTeX `bin` folder, in place of the bundle's. |
| `MATHIMATOR_SHELL_READY_FILE` | shell | When it names a file, the shell writes `ready <unix ms>` there once the UI is up. The startup timing measurements read it. It is the only one of these variables that a release shell reads. |
| `MATHIMATOR_TEX_ERROR` | worker | Set by the shell, never by hand: it sets or clears it at every worker start, as the reason formulas cannot render. The worker then refuses jobs that need LaTeX with `tex_unavailable` and that reason, until the shell's `set_tex_error` request replaces it. |
| `MATHIMATOR_TEST_*` | Win32 stand-ins | Set by the Job Object tests for the C# stand-in programs in `tests/fixtures/win32`. |

The three tool overrides, `MATHIMATOR_PYTHON`, `MATHIMATOR_TYPST` and
`MATHIMATOR_TINYTEX_BIN`, apply to debug builds only. A release shell uses only
its installed runtime. It removes every inherited `MATHIMATOR_*` variable from
the environment of the worker and of the TeX self-test, and names each one but
`MATHIMATOR_SHELL_READY_FILE` in `shell.log`:

```text
Ignored <NAME>: release builds neither read it nor pass it to the worker.
```

A Typst or TinyTeX override that points at nothing disables only the glyph
preview or the formulas.

## Making a change

- Open an issue first for anything larger than a fix, and for any new
  dependency: every runtime dependency ships in the installer and its notices.
- Behaviour follows `docs/design-spec.md`. A change of behaviour updates the
  spec in the same merge request.
- Every fix comes with a test. Run the fast lane and the tiers your change
  touches; the README lists them. On its merge request, start the manual
  Windows jobs that cover it, as [CI minutes](#ci-minutes) describes.
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
- A change to the export formats, sizes or frame rates, their opaque-format
  messages, `MIN_BEAT_SECONDS` or `MAX_TOTAL_SECONDS` in
  `worker/mathimator_worker/document.py` updates
  `tests/fixtures/protocol/export-choices.json`, which the Python and
  TypeScript tests check.
- The pinned versions change only by hand and in every copy at once: Manim
  0.22.0, Typst 0.15.0, CPython 3.14.9 with 3.13.17 as the fallback, uv
  0.13.5, Rust 1.93.0, Node 24, Tauri 2.12.x, React 19 and Vite 8.
  `scripts/check_versions.py` and `ci/test_pipeline.py` fail CI when the
  copies of the Manim, Typst, CPython, uv, Rust or Node pin disagree; the
  Tauri, React and Vite pins are kept in step by review. A change to a version
  the spec names is also a spec revision. Renovate proposes none of these
  changes.
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

The scopes in use are `worker`, `ui`, `shell`, `packaging`, `deps` and
`spec`, for example `fix(worker): refuse a newer generatorVersion`. Mark a
breaking change with `!` after the type or scope, or with a `BREAKING CHANGE:`
footer.

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
- `.gitlab/CODEOWNERS` names the owner of each area. On GitLab Free it is
  information only: it requires no approval and blocks no merge.

## CI minutes

GitLab Free gives the namespace about 400 compute minutes a month, so the
pipeline spends them only where a change needs them. The rules are in
`.gitlab-ci.yml` and the `ci/*.yml` files, and `ci/test_pipeline.py` checks
them:

- A push to a branch runs no pipeline of its own; its merge request does.
- A merge request runs `lint:repo`, SAST and Secret Detection, and the jobs of
  each lane whose files it changes: the Python, Typst, UI and Rust lanes, the
  dependency audits and the Windows fast lane (`test:windows`). The tools and
  dependencies come from caches.
- After a merge, `main` runs only `lint:repo`, SAST and Secret Detection: with
  semi-linear merges its tree is the one the merge request pipeline passed.
- There is no nightly pipeline. The one schedule runs Renovate, and an
  advisory against an unchanged lockfile shows at the next change to that
  lockfile or at the next tag.
- The Windows Rust build (`test:rust:windows`), the render, export and offline
  suites (`test:render:windows`) and the installer (`package:windows`) run on
  every tag, and otherwise only when someone starts them from the pipeline
  page. Linux never compiles the shell's `cfg(windows)` code, so start
  `test:rust:windows` in your merge request's pipeline when you change the
  shell's Windows code, and `test:render:windows` when you change rendering,
  export, the bundle or its pins. Manual jobs never block a merge.

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
4. The tag pipeline runs every job, the Windows Rust build, the render lane
   and the installer included. `lint:repo` fails unless the tag is `v`
   followed by the app version, and `package:windows` builds the installer.
5. Once every job passes, the release jobs upload the installer,
   `THIRD_PARTY_NOTICES.txt` and `SHA256SUMS` to the project's generic package
   registry, and create a GitLab Release that links them, with notes git-cliff
   writes from the commits since the previous tag. GitLab creates the Release
   with glab, in the `registry.gitlab.com/gitlab-org/cli` image.

v1 installers are unsigned. Signing needs no code change: setting the protected
CI/CD variable `WINDOWS_SIGN_COMMAND` makes `ci/package-windows.ps1` sign the
app and the installer (the script describes the format). The installer ships
GPL-licensed encoders inside PyAV's FFmpeg; its notices get a legal review
before the first public release.

## Setting up the GitLab project

No GitLab project exists yet (D65). Once one is created on gitlab.com's Free
tier:

1. Protect `main`, with no pushes allowed and merges by maintainers, and
   protect the tags `v*`, which only maintainers create. The release jobs run
   only for a protected tag.
2. Set the merge method to "Merge commit with semi-linear history", do not
   allow squashing, and allow merges only after a successful pipeline.
3. Keep the gitlab.com instance runners on: the Linux ones, and the hosted
   Windows runner that the `saas-windows-medium-amd64` tag selects.
4. Add the masked and protected CI/CD variables that `ci/renovate.yml`
   describes:
   - `RENOVATE_TOKEN`: the Free tier has no project access tokens, so this is
     a personal access token of a bot user that is a member of the project,
     with the `api`, `read_user` and `write_repository` scopes;
   - `GITHUB_COM_TOKEN`: a read-only GitHub token for Renovate's release and
     changelog lookups on github.com.

   Leave `WINDOWS_SIGN_COMMAND` unset: v1 installers are unsigned (D74).
5. Create one pipeline schedule on `main` that sets the variable
   `SCHEDULE_KIND=renovate`; any other schedule gets no pipeline. Each run
   spends Linux compute minutes, so choose its interval with the budget in
   mind.
6. Add the project's address to `SECURITY.md`, which says only "Mathimator's
   GitLab project", and to the written source offer in
   `scripts/generate_notices.py`, which says only "the Mathimator issue
   tracker".
7. Watch the first pipelines for what no local check can show: that the image
   tags exist, that the hosted Windows image brings Git, curl.exe and the C++
   Build Tools, how native stderr behaves under its PowerShell, that the jobs
   finish within their timeouts, that GitLab maps the UI's Cobertura paths
   onto `desktop/src`, and how SAST and Renovate treat the repository.

`.gitlab/CODEOWNERS` needs no setting: on the Free tier it is information
only. `release:publish` uses the glab image's `latest` tag, because GitLab.com
writes the glab command line it runs. To pin a release instead, look up the
newest tag of `registry.gitlab.com/gitlab-org/cli`, set it in
`ci/release.yml`, and tighten `test_the_release_is_created_with_glab` in
`ci/test_pipeline.py` to match.
