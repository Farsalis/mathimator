"""The GitHub Actions workflows in .github/workflows hold together.

These checks catch what GitHub reports only when it runs a workflow, and what
it never reports: a script or path that a workflow names and the repository
lacks, an action that no release pins, a token with more permissions than its
job needs, a tool pin that disagrees with its source, a download the scripts
check against no SHA-256, a pre-commit SKIP or a pytest marker that names
nothing, a lane that misses a file its tests or linters read, a Windows test
job without the Win32 tests, a folder CI writes that git would pick up, a
release that publishes itself, triggers other than the chosen ones, and
Dependabot rules that would move the pins the spec fixes.
"""

from __future__ import annotations

import ast
import configparser
import functools
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any

import changed_lanes
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
# The hosted runner images the jobs run on, pinned rather than -latest.
RUNNERS = {"ubuntu-24.04", "windows-2025"}
# The jobs that need more than to read the repository, and what they need.
WRITERS = {
    ("codeql.yml", "analyze"): {"contents": "read", "security-events": "write"},
    ("release.yml", "draft"): {"contents": "write"},
}
# The jobs of ci.yml that run every time, whatever a pull request changes.
EVERY_CHANGE = {"changes", "python-lint", "hygiene", "result"}
# The lane jobs that a pull request starts when it changes one file.
PYTHON = {"python-tests"}
TYPST = {"typst"}
WINDOWS = {"python-tests-windows"}
UI = {"ui"}
RUST = {"rust-linux", "rust-windows"}
RUST_WINDOWS = {"rust-windows"}
AUDITS = {"audit-python", "audit-npm", "audit-rust"}
EVERY_LANE = PYTHON | TYPST | WINDOWS | UI | RUST | AUDITS
STARTED = {
    "CONTRIBUTING.md": set(),
    "README.md": set(),
    ".github/workflows/slow.yml": set(),
    ".github/dependabot.yml": set(),
    "docs/design-spec.md": PYTHON | WINDOWS,
    "packaging/build-bundle.ps1": PYTHON | WINDOWS,
    "packaging/licenses/MIT.txt": PYTHON | WINDOWS,
    "worker/mathimator_worker/session.py": PYTHON | TYPST | WINDOWS | RUST_WINDOWS,
    "worker/pyproject.toml": PYTHON | TYPST | WINDOWS | RUST_WINDOWS | {"audit-python"},
    "tests/test_protocol.py": PYTHON | TYPST | WINDOWS,
    "scenes/reference_60.py": PYTHON | WINDOWS,
    "scripts/check_bundle.py": PYTHON | WINDOWS,
    "pytest.ini": PYTHON | TYPST | WINDOWS,
    ".python-version": PYTHON | TYPST | WINDOWS | RUST_WINDOWS,
    "packaging/versions.env": PYTHON | TYPST | WINDOWS,
    "pyproject.toml": PYTHON | TYPST | WINDOWS | {"audit-python"},
    "uv.lock": PYTHON | TYPST | WINDOWS | {"audit-python"},
    "ci/test_pipeline.py": WINDOWS,
    "ci/install-typst.sh": TYPST,
    "ci/windows-tools.ps1": WINDOWS | RUST_WINDOWS,
    "ci/deny.toml": {"audit-rust"},
    "desktop/src/App.tsx": UI,
    "desktop/.nvmrc": UI,
    "desktop/package-lock.json": UI | {"audit-npm"},
    "worker/mathimator_worker/supervisor.py": PYTHON | TYPST | WINDOWS | UI | RUST_WINDOWS,
    "tests/fixtures/timing/frame-boundaries.json": PYTHON | TYPST | WINDOWS | UI,
    "tests/fixtures/protocol/commands.json": PYTHON | TYPST | WINDOWS | UI | RUST,
    "tests/fixtures/win32/sleeper.cs": PYTHON | TYPST | WINDOWS | RUST,
    "desktop/src-tauri/src/lib.rs": RUST,
    "desktop/src-tauri/tauri.conf.json": RUST | UI,
    "desktop/src-tauri/capabilities/default.json": RUST | UI,
    "desktop/src-tauri/Cargo.lock": RUST | {"audit-rust"},
    ".github/workflows/ci.yml": EVERY_LANE,
    "ci/changed_lanes.py": EVERY_LANE,
}
# The folders the jobs write: build output, reports, the bundle and its cache.
OUTPUTS = (
    "out/",
    "reports/",
    "desktop/dist/",
    "desktop/reports/",
    "desktop/coverage/",
    "packaging/bundle/",
    "packaging/.cache",
)
# A path the workflows name, relative to the repository root.
PATH = re.compile(r"(?<![\w./$\\-])((?:\.github|ci|desktop|docs|packaging|scenes|scripts|tests|worker)/[\w.*/-]*)")
# The suffixes of the files Prettier formats.
PRETTIER = {".cjs", ".css", ".html", ".js", ".json", ".jsx", ".md", ".mjs", ".ts", ".tsx", ".yaml", ".yml"}


class _Loader(yaml.SafeLoader):
    """Reads YAML as GitHub does, and refuses a mapping that repeats a key."""


def _mapping(loader: _Loader, node: yaml.MappingNode) -> dict[Any, Any]:
    keys = [loader.construct_object(key) for key, _value in node.value]
    repeated = sorted({str(key) for key in keys if keys.count(key) > 1})
    if repeated:
        raise yaml.constructor.ConstructorError(None, None, f"repeated keys {repeated}", node.start_mark)
    return loader.construct_mapping(node)


# YAML 1.1 reads on, off, yes and no as booleans, so `on:` would load as True.
# GitHub reads only true and false that way.
_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _load(path: Path) -> Any:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


@functools.cache
def _workflows() -> dict[str, dict[str, Any]]:
    """Every workflow, by file name."""
    return {path.name: _load(path) for path in sorted(WORKFLOWS.glob("*.yml"))}


def _jobs() -> Iterator[tuple[str, str, dict[str, Any]]]:
    for name, workflow in _workflows().items():
        for job_id, job in workflow["jobs"].items():
            yield name, job_id, job


def _job(workflow: str, job_id: str) -> dict[str, Any]:
    job: dict[str, Any] = _workflows()[workflow]["jobs"][job_id]
    return job


def _env(workflow: str, job_id: str) -> dict[str, Any]:
    return {**_workflows()[workflow].get("env", {}), **_job(workflow, job_id).get("env", {})}


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = job.get("steps", [])
    return steps


def _uses(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    """The steps of a job that use an action, at any version."""
    return [step for step in _steps(job) if str(step.get("uses", "")).split("@")[0] == action]


def _runs(job: dict[str, Any]) -> list[tuple[str, str]]:
    """Each command line of a job's run steps, with its working directory."""
    default = job.get("defaults", {}).get("run", {}).get("working-directory", ".")
    return [
        (line.strip(), step.get("working-directory", default))
        for step in _steps(job)
        if "run" in step
        for line in step["run"].splitlines()
        if line.strip()
    ]


def _commands(job: dict[str, Any]) -> list[str]:
    return [line for line, _directory in _runs(job)]


def _index(job: dict[str, Any], prefix: str) -> int:
    """The position of the first command line that starts with prefix."""
    indexes = [index for index, line in enumerate(_commands(job)) if line.startswith(prefix)]
    assert indexes, f"no command starts with {prefix!r}"
    return indexes[0]


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _lane_jobs() -> dict[str, str]:
    """The lane of each ci.yml job that runs only when its lane does."""
    lanes = {}
    for job_id, job in _workflows()["ci.yml"]["jobs"].items():
        if job_id not in EVERY_CHANGE:
            match = re.fullmatch(r"needs\.changes\.outputs\.(\w+) == 'true'", job.get("if", ""))
            assert _needs(job) == ["changes"] and match, f"{job_id} runs on no lane"
            lanes[job_id] = match[1]
    return lanes


def _started(path: str) -> set[str]:
    """The lane jobs that a pull request changing path starts."""
    selected = changed_lanes.lanes([path])
    return {job_id for job_id, lane in _lane_jobs().items() if selected[lane]}


def _tracked(pathspec: str) -> list[str]:
    """The files git tracks that the pathspec matches."""
    command = ["git", "ls-files", "-z", "--", pathspec]
    listed = subprocess.run(command, cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return listed.split("\0")[:-1]


def _ignored(paths: Iterable[str]) -> set[str]:
    """The paths that git ignores, by its own reading of .gitignore."""
    command = ["git", "check-ignore", "--no-index", "--stdin", "-z"]
    completed = subprocess.run(
        command, cwd=REPO, input="\0".join(paths), capture_output=True, text=True, encoding="utf-8"
    )
    # git check-ignore exits with 1 when it ignores none of the paths.
    assert completed.returncode in (0, 1), completed.stderr
    return set(completed.stdout.split("\0")[:-1])


def _example(pattern: str) -> str:
    """A path that a glob pattern matches."""
    return pattern.replace("**", "x").replace("*", "x")


def _pins() -> dict[str, str]:
    text = (REPO / "packaging" / "versions.env").read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", text, re.MULTILINE))


def _project() -> dict[str, Any]:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _pre_commit() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return config


def _exists(path: str) -> bool:
    path = path.rstrip("/")
    return any(REPO.glob(path)) if "*" in path else (REPO / path).exists()


def test_the_workflows_are_ci_slow_release_and_codeql() -> None:
    assert sorted(_workflows()) == ["ci.yml", "codeql.yml", "release.yml", "slow.yml"]
    for name, workflow in _workflows().items():
        assert {"name", "on", "permissions", "jobs"} <= workflow.keys(), name


def test_every_job_runs_on_a_pinned_runner_with_a_timeout() -> None:
    for workflow, job_id, job in _jobs():
        if "uses" in job:
            assert job["uses"].startswith("./.github/workflows/"), (workflow, job_id)
            continue
        assert job["runs-on"] in RUNNERS, (workflow, job_id)
        assert isinstance(job.get("timeout-minutes"), int), (workflow, job_id)


def test_actions_are_pinned_to_releases() -> None:
    # A full commit SHA or a release tag, never a branch. Dependabot moves them.
    for workflow, job_id, job in _jobs():
        for uses in [job.get("uses"), *(step.get("uses") for step in _steps(job))]:
            if uses is None:
                continue
            if uses.startswith("./"):
                assert (REPO / uses).is_file(), (workflow, job_id, uses)
            else:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@(?:[0-9a-f]{40}|v\d+(?:\.\d+){0,2})", uses), (workflow, uses)


def test_tokens_have_least_privilege() -> None:
    for name, workflow in _workflows().items():
        assert workflow["permissions"] == {"contents": "read"}, name
    for workflow, job_id, job in _jobs():
        if (workflow, job_id) in WRITERS:
            assert job["permissions"] == WRITERS[workflow, job_id], (workflow, job_id)
        else:
            assert "permissions" not in job, (workflow, job_id)
        # No later step can push with the checkout's token.
        for step in _uses(job, "actions/checkout"):
            assert step["with"]["persist-credentials"] is False, (workflow, job_id)


def test_the_workflows_run_on_the_chosen_triggers() -> None:
    triggers = {name: workflow["on"] for name, workflow in _workflows().items()}
    assert triggers["ci.yml"] == {"pull_request": None, "push": {"branches": ["main"]}, "workflow_call": None}
    assert triggers["codeql.yml"].keys() == {"pull_request", "push", "schedule"}
    assert triggers["codeql.yml"]["push"] == {"branches": ["main"]}
    # The slow lanes run every night, by hand, and for every tag through the
    # release workflow, which calls them.
    assert triggers["slow.yml"].keys() == {"schedule", "workflow_dispatch", "workflow_call"}
    (nightly,) = [entry["cron"] for entry in triggers["slow.yml"]["schedule"]]
    assert nightly.split()[2:] == ["*", "*", "*"], nightly
    assert triggers["release.yml"] == {"push": {"tags": ["v*"]}}
    release = _workflows()["release.yml"]["jobs"]
    called = {job["uses"]: job_id for job_id, job in release.items() if "uses" in job}
    assert called == {"./.github/workflows/ci.yml": "ci", "./.github/workflows/slow.yml": "slow"}
    assert set(called.values()) <= set(release["draft"]["needs"])


def test_superseded_pull_request_runs_are_cancelled() -> None:
    # A called workflow evaluates its concurrency group in the caller's
    # context, so each workflow's group needs a prefix of its own: a shared one
    # would make the release wait for itself.
    prefixes = []
    for name, workflow in _workflows().items():
        group, cancel = workflow["concurrency"]["group"], workflow["concurrency"]["cancel-in-progress"]
        prefixes.append(group.split("${{")[0])
        if "pull_request" in workflow["on"]:
            assert cancel == "${{ github.event_name == 'pull_request' }}", name
            assert "github.event.pull_request.number" in group, name
        else:
            assert cancel is False, name
    assert len(set(prefixes)) == len(prefixes) and all(prefixes), prefixes


def test_the_files_the_workflows_name_exist() -> None:
    named: set[str] = set()
    for _name, workflow in _workflows().items():
        for text in _strings(workflow["jobs"]):
            text = re.sub(r"(?<![\w.])\./", "", text)
            named.update(PATH.findall(text))
            for argument in re.findall(r"hashFiles\(([^)]*)\)", text):
                named.update(re.findall(r"'([^']+)'", argument))
    for _workflow, _job_id, job in _jobs():
        for step in _steps(job):
            with_ = step.get("with", {})
            for key in ("node-version-file", "cache-dependency-path", "cache-dependency-glob", "workspaces"):
                if key in with_:
                    named.add(with_[key].split(" -> ")[0])
            if "working-directory" in step:
                named.add(step["working-directory"])
    assert {"ci/install-typst.sh", "ci/windows-tools.ps1", "ci/package-windows.ps1", "ci/deny.toml"} <= named
    assert {"scripts/check_versions.py", "packaging/build-bundle.ps1", "uv.lock", "desktop/.nvmrc"} <= named
    assert sorted(path for path in named if not path.startswith(OUTPUTS) and not _exists(path)) == []


def test_uv_is_the_release_that_the_project_requires() -> None:
    required = _project()["tool"]["uv"]["required-version"]
    assert required == f"=={_pins()['UV_VERSION']}"
    steps = [step for _workflow, _job_id, job in _jobs() for step in _uses(job, "astral-sh/setup-uv")]
    assert steps
    for step in steps:
        assert step["with"]["version"] == _pins()["UV_VERSION"], step


def test_node_is_the_major_that_nvmrc_names() -> None:
    steps = [step for _workflow, _job_id, job in _jobs() for step in _uses(job, "actions/setup-node")]
    assert steps
    for step in steps:
        assert step["with"]["node-version-file"] == "desktop/.nvmrc", step
        assert "node-version" not in step["with"], step


def test_cargo_runs_the_toolchain_that_rust_toolchain_toml_pins() -> None:
    # rustup installs the toolchain and components that the file in
    # desktop/src-tauri names, on Linux directly and on Windows through
    # ci/windows-tools.ps1 -Rust, before any cargo command of the shell runs.
    jobs = 0
    for workflow, job_id, job in _jobs():
        runs = _runs(job)
        shell = [
            index
            for index, (line, directory) in enumerate(runs)
            if (line.startswith("cargo ") and directory == "desktop/src-tauri") or "ci/package-windows.ps1" in line
        ]
        if not shell:
            continue
        jobs += 1
        installs = [
            index
            for index, (line, directory) in enumerate(runs)
            if (line == "rustup toolchain install" and directory == "desktop/src-tauri")
            or (line.startswith("./ci/windows-tools.ps1 ") and "-Rust" in line.split())
        ]
        assert installs and installs[0] < shell[0], (workflow, job_id)
        assert not any(re.search(r"\bcargo \+", line) for line, _directory in runs), (workflow, job_id)
        assert not any(str(step.get("uses", "")).startswith("dtolnay/") for step in _steps(job)), (workflow, job_id)
    assert jobs == 3


def test_the_linux_python_lane_runs_the_bundle_interpreters() -> None:
    job = _job("ci.yml", "python-tests")
    assert set(job["strategy"]["matrix"]["python"]) == {_pins()["PYTHON_VERSION"], _pins()["PYTHON_FALLBACK_VERSION"]}
    assert job["env"]["UV_PYTHON"] == "${{ matrix.python }}"


def test_windows_test_jobs_run_the_win32_tests() -> None:
    windows = []
    for workflow, job_id, job in _jobs():
        tests = any(re.search(r"\b(pytest|cargo test)\b", line) for line in _commands(job))
        if job.get("runs-on", "").startswith("windows-") and tests:
            windows.append(job_id)
            assert _env(workflow, job_id).get("MATHIMATOR_WIN32_TESTS") == "1", (workflow, job_id)
    assert sorted(windows) == ["python-tests-windows", "render", "rust-windows"]


def test_the_windows_rust_job_runs_every_ignored_shell_test() -> None:
    # cargo test --include-ignored runs the shell's ignored tests, and each
    # returns early unless the variable its reason names is 1, so every reason
    # names one. Those that start the repository's Python find it in .venv, as
    # a debug shell does.
    source = "\n".join(path.read_text(encoding="utf-8") for path in (REPO / "desktop/src-tauri/src").rglob("*.rs"))
    reasons = re.findall(r'#\[ignore(?: = "([^"]*)")?', source)
    assert all(re.search(r"\bneeds \w+=1\b", reason) for reason in reasons), reasons
    gates = {gate for reason in reasons for gate in re.findall(r"\bneeds (\w+)=1\b", reason)}
    assert "MATHIMATOR_REAL_WORKER_TESTS" in gates
    env = _env("ci.yml", "rust-windows")
    assert {gate: env.get(gate) for gate in gates} == dict.fromkeys(gates, "1")
    job = _job("ci.yml", "rust-windows")
    tools = _index(job, "./ci/windows-tools.ps1 ")
    assert "-Uv" in _commands(job)[tools].split()
    assert tools < _index(job, "uv venv") < _index(job, "cargo test --locked -- --include-ignored")


def test_the_windows_fast_lane_runs_pytest_in_a_short_folder() -> None:
    # The worker refuses a project path longer than 114 characters while
    # Windows long paths are off, and a test project under the runner
    # profile's temp folder can pass that. pytest creates --basetemp but not
    # its parent, so the folder sits at the drive root.
    job = _job("ci.yml", "python-tests-windows")
    pytest = _commands(job)[_index(job, "uv run pytest ")]
    assert re.search(r" --basetemp=[A-Z]:\\\w+ ", pytest)


def test_marker_expressions_name_registered_markers() -> None:
    ini = configparser.ConfigParser()
    ini.read(REPO / "pytest.ini", encoding="utf-8")
    markers = {line.split(":")[0].strip() for line in ini["pytest"]["markers"].splitlines() if line.strip()}
    lines = [line for _workflow, _job_id, job in _jobs() for line in _commands(job)]
    expressions = [quoted or bare for line in lines for quoted, bare in re.findall(r'-m (?:"([^"]+)"|(\w+))', line)]
    assert len(expressions) == 4
    for expression in expressions:
        unknown = set(re.findall(r"\w+", expression)) - {"and", "or", "not"} - markers
        assert not unknown, f"-m {expression!r} names unregistered markers {sorted(unknown)}"


def test_the_slow_lanes_run_the_render_suites_and_build_the_installer() -> None:
    render = _job("slow.yml", "render")
    build = _index(render, "powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging/build-bundle.ps1")
    tests = _index(render, 'uv run pytest tests -m "render or export or offline" ')
    assert build < tests
    # An xfail that passes fails the lane.
    assert " -o xfail_strict=true " in _commands(render)[tests]
    installer = _job("slow.yml", "installer")
    assert "./ci/package-windows.ps1" in _commands(installer)
    (upload,) = [
        step for step in _uses(installer, "actions/upload-artifact") if step["with"]["name"] == "windows-installer"
    ]
    assert upload["with"]["path"] == "out/package/"
    assert "if" not in upload


def test_pre_commit_skips_name_hooks_and_ci_runs_the_rest() -> None:
    hooks = {hook["id"] for repo in _pre_commit()["repos"] for hook in repo["hooks"]}
    skipped = set(_job("ci.yml", "hygiene")["env"]["SKIP"].split(","))
    assert skipped <= hooks, f"SKIP names no hook: {sorted(skipped - hooks)}"
    assert {"check-yaml", "check-github-workflows", "check-dependabot"} <= hooks - skipped
    assert "uv run pre-commit run --all-files --show-diff-on-failure" in _commands(_job("ci.yml", "hygiene"))


def test_the_mypy_hooks_install_what_the_checked_files_import() -> None:
    # A hook runs mypy in an environment of its own, which holds its
    # additional_dependencies alone: mypy, and each package that a file mypy
    # checks imports, at the dev group's pins. CI skips these hooks, so
    # nothing else would notice a missing one.
    project = _project()
    pins = {re.split(r"[^\w-]", pin, maxsplit=1)[0]: pin for pin in project["dependency-groups"]["dev"]}
    checked = [path for folder in project["tool"]["mypy"]["files"] for path in (REPO / folder).rglob("*.py")]
    imported = {
        name
        for path in checked
        for name in re.findall(r"^\s*(?:import|from) (\w+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    }
    local = {path.stem for path in checked} | {path.parent.name for path in checked}
    packages = {"mypy", *(imported - set(sys.stdlib_module_names) - local)}
    assert "pytest" in packages
    hooks = [hook for repo in _pre_commit()["repos"] for hook in repo["hooks"] if hook.get("entry") == "mypy"]
    assert [hook["id"] for hook in hooks] == ["mypy", "mypy-linux"]
    for hook in hooks:
        assert sorted(hook["additional_dependencies"]) == sorted(pins[name] for name in packages), hook["id"]


def test_the_ruff_hook_is_the_dev_group_ruff() -> None:
    (pin,) = [pin for pin in _project()["dependency-groups"]["dev"] if pin.startswith("ruff==")]
    (repo,) = [
        repo for repo in _pre_commit()["repos"] if repo["repo"] == "https://github.com/astral-sh/ruff-pre-commit"
    ]
    assert repo["rev"] == f"v{pin.removeprefix('ruff==')}"


def test_every_download_the_scripts_check_has_a_pinned_sha256() -> None:
    # A script stops at a download whose pin is empty, so an empty pin fails
    # the typst job and every bundle build, wherever they run.
    scripts = "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in ("ci/*.sh", "ci/*.ps1", "packaging/*.sh", "packaging/*.ps1")
        for path in REPO.glob(pattern)
    )
    names = set(re.findall(r"\b[A-Z]+_(?:WINDOWS|LINUX)_SHA256\b", scripts))
    assert names
    for name in sorted(names):
        assert re.fullmatch(r"[0-9a-f]{64}", _pins().get(name, "")), f"packaging/versions.env pins no SHA-256 {name}"


def test_the_typst_lane_installs_the_pinned_typst() -> None:
    job = _job("ci.yml", "typst")
    install = _index(job, 'bash ci/install-typst.sh "$RUNNER_TEMP/typst"')
    assert _commands(job)[install + 1] == 'echo "$RUNNER_TEMP/typst" >> "$GITHUB_PATH"'
    assert install < _index(job, "uv run pytest tests -m typst ")


def test_the_lane_jobs_read_the_lanes_of_the_changes_job() -> None:
    jobs = _workflows()["ci.yml"]["jobs"]
    outputs = jobs["changes"]["outputs"]
    assert outputs == {lane: f"${{{{ steps.lanes.outputs.{lane} }}}}" for lane in changed_lanes.LANES}
    assert set(_lane_jobs().values()) == set(changed_lanes.LANES)
    assert set(jobs) == EVERY_CHANGE | set(_lane_jobs())
    for job_id in EVERY_CHANGE - {"result"}:
        assert "if" not in jobs[job_id] and not _needs(jobs[job_id]), job_id
    # The result job waits for every other job, whatever their outcome.
    assert jobs["result"]["if"] == "always()"
    assert sorted(_needs(jobs["result"])) == sorted(set(jobs) - {"result"})


def test_a_pull_request_starts_the_lanes_of_its_file() -> None:
    for path, started in STARTED.items():
        assert _started(path) == started, path


def test_the_lane_patterns_are_globs() -> None:
    assert changed_lanes.matches("desktop/package.json", ["desktop/*"])
    assert not changed_lanes.matches("desktop/src/App.tsx", ["desktop/*"])
    assert changed_lanes.matches("desktop/src-tauri/tauri.conf.json", ["desktop/src-tauri/**/*.json"])
    assert changed_lanes.matches("worker/a/b.py", ["worker/**"])
    assert not changed_lanes.matches("ci/sub/x.py", ["ci/*.py"])
    assert not changed_lanes.matches("uv.lock.bak", ["uv.lock"])


def test_the_lane_script_speaks_github_output_on_the_runner_python() -> None:
    script = REPO / "ci" / "changed_lanes.py"
    # The changes job runs it on the runner image's python3, 3.12 on Ubuntu 24.04.
    ast.parse(script.read_text(encoding="utf-8"), feature_version=(3, 12))
    paths = b"desktop/src/App.tsx\0ci/deny.toml\0"
    output = subprocess.run([sys.executable, str(script)], input=paths, capture_output=True, check=True).stdout
    expected = {lane: lane in {"ui", "audit_rust"} for lane in changed_lanes.LANES}
    assert output.decode().splitlines() == [f"{lane}={str(runs).lower()}" for lane, runs in expected.items()]
    output = subprocess.run([sys.executable, str(script), "--all"], capture_output=True, check=True).stdout
    assert output.decode().splitlines() == [f"{lane}=true" for lane in changed_lanes.LANES]
    command = _job("ci.yml", "changes")["steps"][1]["run"]
    assert "git diff --name-only --no-renames -z HEAD^1 HEAD | python3 ci/changed_lanes.py" in command
    assert _job("ci.yml", "changes")["steps"][0]["with"]["fetch-depth"] == 2


def test_the_lanes_cover_the_files_their_tests_read() -> None:
    # The UI and the shell name files relative to the source that reads them,
    # the Python tests relative to the repository. A glob the UI imports
    # stands for a file it matches.
    relative = (
        (
            "ui",
            "desktop/src",
            {".ts", ".tsx"},
            r"""(?:from|import|import\.meta\.glob(?:<[^>]*>)?)\s*\(?\s*["'](\.{1,2}/[^"'?]+)""",
        ),
        ("rust", "desktop/src-tauri/src", {".rs"}, r'include_(?:str|bytes)!\(\s*"([^"]+)"'),
    )
    reads = {
        lane: {
            (path.parent / _example(target)).resolve().relative_to(REPO).as_posix()
            for path in (REPO / folder).rglob("*")
            if path.suffix in suffixes
            for target in re.findall(pattern, path.read_text(encoding="utf-8"))
        }
        for lane, folder, suffixes, pattern in relative
    }
    reads["python"] = {
        "/".join(re.findall(r'"([^"]+)"', chain))
        for path in [*(REPO / "tests").glob("*.py"), *(REPO / "scripts").glob("test_*.py")]
        for chain in re.findall(r'REPO((?: / "[^"]+")+)', path.read_text(encoding="utf-8"))
    }
    for lane, paths in reads.items():
        assert any(path.startswith("tests/fixtures/") for path in paths), lane
        changes = {f"{path}/x" if (REPO / path).is_dir() else path for path in paths}
        for changed in sorted(changes - _ignored(changes)):
            assert changed_lanes.lanes([changed])[lane], f"the {lane} lane skips a change to {changed}"


def test_the_checks_that_span_the_lanes_run_every_time() -> None:
    # Ruff and the ci/ tests read every Python file and the workflows, so they
    # need no lane.
    commands = _commands(_job("ci.yml", "python-lint"))
    for command in (
        "uv run python scripts/check_versions.py",
        "uv run ruff check --output-format=github",
        "uv run ruff format --check",
        "uv run mypy",
        "uv run mypy --platform linux",
        "uv lock --check",
        "uv run pytest ci --junitxml=reports/junit-ci.xml",
    ):
        assert command in commands, command
    excluded = _project()["tool"]["ruff"]["extend-exclude"]
    checked = [path for path in _tracked("*.py") if not any(PurePosixPath(path).is_relative_to(x) for x in excluded)]
    assert "ci/test_pipeline.py" in checked


def test_prettier_checks_every_desktop_file_a_pull_request_changes() -> None:
    # The ui job runs `prettier --check .` in desktop/, which reaches the
    # shell's JSON configuration too.
    formatted = [path for path in _tracked("desktop") if PurePosixPath(path).suffix in PRETTIER]
    assert "desktop/src-tauri/tauri.conf.json" in formatted
    for path in formatted:
        assert "ui" in _started(path), path


def test_the_ui_lane_keeps_its_junit_and_coverage_reports() -> None:
    job = _job("ci.yml", "ui")
    assert job["defaults"]["run"]["working-directory"] == "desktop"
    assert "npm run test:ci" in _commands(job)
    vite = (REPO / "desktop" / "vite.config.ts").read_text(encoding="utf-8")
    junit = re.search(r'junit: "([^"]+)"', vite)
    assert junit and re.search(r'reporter: \[[^\]]*"cobertura"', vite)
    (upload,) = _uses(job, "actions/upload-artifact")
    assert upload["if"] == "always()"
    folders = upload["with"]["path"].split()
    assert f"desktop/{PurePosixPath(junit[1]).parent}/" in folders and "desktop/coverage/" in folders


def test_git_ignores_what_the_jobs_leave_in_the_checkout() -> None:
    # The folders that ci/windows-tools.ps1 installs into, the cache and
    # artifact paths in the checkout, and the files that pytest --cov,
    # `npm run test:ci`, `uv venv` and the worker build write.
    tools = (REPO / "ci" / "windows-tools.ps1").read_text(encoding="utf-8")
    kept = {f"{folder}/x" for folder in re.findall(r'Join-Path \$Root "(\.[^"]+)"', tools)}
    for _workflow, _job_id, job in _jobs():
        for step in [*_uses(job, "actions/upload-artifact"), *_uses(job, "actions/cache")]:
            for path in step["with"]["path"].split():
                if not path.startswith("~"):
                    kept.add(_example(f"{path}x" if path.endswith("/") else path))
    junit = re.search(r'junit: "([^"]+)"', (REPO / "desktop" / "vite.config.ts").read_text(encoding="utf-8"))
    assert junit
    kept |= {
        ".coverage",
        ".coverage.host.123.456",
        ".venv/x",
        "desktop/dist/index.html",
        "desktop/coverage/index.html",
        f"desktop/{junit[1]}",
        "out/worker-dist/x",
        "reports/junit-ci.xml",
    }
    assert {".tools/x", ".cargo/x", "packaging/.cache", "out/package/x"} <= kept
    assert sorted(kept - _ignored(kept)) == []


def test_the_release_is_a_draft_with_the_installer_and_its_notes() -> None:
    jobs = _workflows()["release.yml"]["jobs"]
    assert 'uv run python scripts/check_versions.py --tag "$GITHUB_REF_NAME"' in _commands(jobs["versions"])
    # git-cliff does not create the folder it writes to.
    notes = _commands(jobs["notes"])
    cliff = _index(jobs["notes"], "uv run git-cliff ")
    output = re.search(r"--output (\S+)", notes[cliff])
    assert output and f"mkdir -p {PurePosixPath(output[1]).parent}" in notes[:cliff]
    assert _uses(jobs["notes"], "actions/checkout")[0]["with"]["fetch-depth"] == 0
    (uploaded,) = _uses(jobs["notes"], "actions/upload-artifact")
    assert uploaded["with"]["path"] == output[1]
    # The draft job downloads what the notes job and the slow installer job
    # upload, checks it, and creates a draft that a maintainer publishes.
    downloads = {
        step["with"]["name"]: step["with"]["path"] for step in _uses(jobs["draft"], "actions/download-artifact")
    }
    assert downloads == {
        "windows-installer": "out/package",
        "release-notes": PurePosixPath(output[1]).parent.as_posix(),
    }
    draft = " ".join(_commands(jobs["draft"]))
    assert "sha256sum --check SHA256SUMS" in draft
    assert re.search(r'gh release create "\$TAG" out/package/\* --draft ', draft)
    assert f"--notes-file {output[1]}" in draft
    package = (REPO / "ci" / "package-windows.ps1").read_text(encoding="utf-8")
    assert '"out\\package"' in package and '"SHA256SUMS"' in package and '"THIRD_PARTY_NOTICES.txt"' in package
    # Nothing else makes or publishes a release.
    for workflow, job_id, job in _jobs():
        for line in _commands(job):
            if "gh release" in line or "git push" in line:
                assert (workflow, job_id) == ("release.yml", "draft"), line
            assert "--draft=false" not in line


def test_the_signing_hook_stays_off_until_the_repository_variable_is_set() -> None:
    assert _env("slow.yml", "installer")["WINDOWS_SIGN_COMMAND"] == "${{ vars.WINDOWS_SIGN_COMMAND }}"
    assert "$env:WINDOWS_SIGN_COMMAND" in (REPO / "ci" / "package-windows.ps1").read_text(encoding="utf-8")
    tauri = json.loads((REPO / "desktop" / "src-tauri" / "tauri.windows.conf.json").read_text(encoding="utf-8"))
    assert tauri["bundle"]["windows"]["signCommand"] is None
    for workflow, job_id, _job in _jobs():
        if (workflow, job_id) != ("slow.yml", "installer"):
            assert "WINDOWS_SIGN_COMMAND" not in _env(workflow, job_id), (workflow, job_id)


def test_the_audits_check_every_lockfile() -> None:
    python = _commands(_job("ci.yml", "audit-python"))
    export = python.index("uv export --all-groups --no-emit-workspace --output-file reports/audit-requirements.txt")
    assert export < python.index("uv run pip-audit --requirement reports/audit-requirements.txt --disable-pip")
    assert ("npm audit --omit=dev", "desktop") in _runs(_job("ci.yml", "audit-npm"))
    rust = _commands(_job("ci.yml", "audit-rust"))
    assert "cargo deny --manifest-path desktop/src-tauri/Cargo.toml check --config ci/deny.toml" in rust


def test_codeql_analyses_every_language() -> None:
    job = _job("codeql.yml", "analyze")
    assert set(job["strategy"]["matrix"]["language"]) == {"actions", "javascript-typescript", "python", "rust"}
    (init,) = _uses(job, "github/codeql-action/init")
    assert init["with"]["build-mode"] == "none"
    assert len(_uses(job, "github/codeql-action/analyze")) == 1


def test_dependabot_keeps_the_spec_pins() -> None:
    config = _load(REPO / ".github" / "dependabot.yml")
    updates = {update["package-ecosystem"]: update for update in config["updates"]}
    assert {ecosystem: update["directory"] for ecosystem, update in updates.items()} == {
        "uv": "/",
        "npm": "/desktop",
        "cargo": "/desktop/src-tauri",
        "github-actions": "/",
    }
    for ecosystem, update in updates.items():
        assert update["schedule"]["interval"] == "weekly", ecosystem
        assert update["groups"], ecosystem

    def ignored(ecosystem: str) -> dict[str, set[str]]:
        """The update types that each ignore rule names, or all of them."""
        rules = updates[ecosystem]["ignore"]
        kinds = {rule["dependency-name"]: rule.get("update-types", ["version-update:all"]) for rule in rules}
        return {name: {kind.removeprefix("version-update:") for kind in types} for name, types in kinds.items()}

    # Python: the dev tools alone, so the render stack that constraint-
    # dependencies holds and the spec's Manim and Typst move by hand.
    dev = [pin for pin in _project()["dependency-groups"]["dev"] if "==" in pin]
    allowed = sorted(rule["dependency-name"] for rule in updates["uv"]["allow"])
    assert allowed == sorted(pin.split("==")[0] for pin in dev)
    assert ignored("uv") == {"manim": {"all"}, "typst": {"all"}}
    major, minor = "semver-major", "semver-minor"
    npm = ignored("npm")
    assert npm.pop("@tauri-apps/*") == {major, minor}
    assert npm == dict.fromkeys(
        [
            "@types/node",
            "react",
            "react-dom",
            "@types/react",
            "@types/react-dom",
            "vite",
            "@vitejs/plugin-react",
            "vitest",
            "@vitest/coverage-v8",
        ],
        {major},
    )
    assert ignored("cargo") == dict.fromkeys(["tauri", "tauri-build", "tauri-plugin-*"], {major, minor})
