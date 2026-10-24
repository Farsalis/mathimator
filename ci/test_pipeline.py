"""The GitLab pipeline in .gitlab-ci.yml and ci/*.yml holds together.

No GitLab project exists yet to lint the pipeline (D65). These checks catch
what GitLab reports only when it creates a pipeline, and what it never
reports: a job defined twice, a pre-commit SKIP or a pytest marker that names
nothing, a CI pin that disagrees with its source, a download the scripts check
against no SHA-256, rules that start a job in pipelines the decisions keep it
out of or leave out a job that another needs, a lane that misses a file its
tests or linters read, and a folder CI writes that git would pick up.
"""

from __future__ import annotations

import configparser
import functools
import re
import subprocess
import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import yaml

REPO = Path(__file__).resolve().parents[1]
# Top-level keys that configure the pipeline rather than define a job.
GLOBAL_KEYS = frozenset({"include", "stages", "workflow", "variables", "default"})
# The predefined variables of each kind of pipeline, on a project whose
# default branch is main.
PIPELINES = {
    "merge request": {"CI_PIPELINE_SOURCE": "merge_request_event"},
    "default branch": {"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_BRANCH": "main"},
    "branch": {"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_BRANCH": "topic"},
    "tag": {"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_TAG": "v1.0.0", "CI_COMMIT_REF_PROTECTED": "true"},
    "unprotected tag": {"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_TAG": "v1.0.0", "CI_COMMIT_REF_PROTECTED": "false"},
    "schedule": {"CI_PIPELINE_SOURCE": "schedule", "CI_COMMIT_BRANCH": "main"},
    "renovate": {"CI_PIPELINE_SOURCE": "schedule", "CI_COMMIT_BRANCH": "main", "SCHEDULE_KIND": "renovate"},
}
# The pipelines that compare a change with its base: GitLab takes every
# changes: in the others to hold.
COMPARED = ("merge request", "default branch")

# The jobs that a merge request starts by itself when it changes one file (O2):
# lint:repo always, and the jobs of each lane the file is in. The heavy Windows
# jobs wait to be started by hand.
EVERY_CHANGE = {"lint:repo"}
PYTHON = {"lint:python", "test:python"}
TYPST = {"test:typst"}
UI = {"build:ui", "lint:ui", "test:ui"}
RUST = {"build:ui", "test:rust"}
WINDOWS = {"test:windows"}
AUDITS = {"audit:python", "audit:npm", "audit:rust"}
LANES = {
    "docs/phase-1-report.md": set(),
    "CONTRIBUTING.md": set(),
    "docs/design-spec.md": PYTHON,
    "packaging/build-bundle.ps1": PYTHON,
    "packaging/licenses/MIT.txt": PYTHON,
    "worker/mathimator_worker/session.py": PYTHON | TYPST | WINDOWS,
    "tests/test_protocol.py": PYTHON | TYPST | WINDOWS,
    "scenes/reference_60.py": PYTHON | WINDOWS,
    "scripts/check_bundle.py": PYTHON | WINDOWS,
    "pytest.ini": PYTHON | TYPST | WINDOWS,
    ".python-version": PYTHON | TYPST | WINDOWS,
    "packaging/versions.env": PYTHON | TYPST | WINDOWS,
    "pyproject.toml": PYTHON | TYPST | WINDOWS | {"audit:python"},
    "uv.lock": PYTHON | TYPST | WINDOWS | {"audit:python"},
    "ci/test_pipeline.py": WINDOWS,
    "ci/install-typst.sh": TYPST,
    "desktop/src/App.tsx": UI,
    "desktop/.nvmrc": UI,
    "desktop/package-lock.json": UI | {"audit:npm"},
    "worker/mathimator_worker/supervisor.py": PYTHON | TYPST | WINDOWS | UI,
    "tests/fixtures/timing/frame-boundaries.json": PYTHON | TYPST | WINDOWS | UI,
    "tests/fixtures/protocol/commands.json": PYTHON | TYPST | WINDOWS | UI | RUST,
    "tests/fixtures/win32/sleeper.cs": PYTHON | TYPST | WINDOWS | RUST,
    "desktop/src-tauri/src/lib.rs": RUST,
    "desktop/src-tauri/tauri.conf.json": RUST | {"lint:ui"},
    "desktop/src-tauri/Cargo.lock": RUST | {"audit:rust"},
    "ci/deny.toml": {"audit:rust"},
    "ci/linux.yml": PYTHON | TYPST | UI | RUST | AUDITS,
    "ci/windows.yml": WINDOWS,
    "ci/windows-tools.ps1": WINDOWS,
    "ci/security.yml": AUDITS,
    ".gitlab-ci.yml": PYTHON | TYPST | UI | RUST | WINDOWS | AUDITS,
}
# They run on tags and are otherwise started by hand (O2).
HEAVY = {"test:rust:windows", "test:render:windows", "package:windows"}
# The suffixes of the files Prettier formats.
PRETTIER = {".cjs", ".css", ".html", ".js", ".json", ".jsx", ".md", ".mjs", ".ts", ".tsx", ".yaml", ".yml"}


class Reference(NamedTuple):
    """GitLab's !reference [job, key, ...] tag."""

    path: tuple[str, ...]


class _Loader(yaml.SafeLoader):
    """Reads !reference tags, and refuses a mapping that repeats a key."""


def _reference(loader: _Loader, node: yaml.SequenceNode) -> Reference:
    return Reference(tuple(loader.construct_sequence(node)))


def _mapping(loader: _Loader, node: yaml.MappingNode) -> dict[Any, Any]:
    keys = [loader.construct_object(key) for key, _value in node.value]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise yaml.constructor.ConstructorError(None, None, f"repeated keys {repeated}", node.start_mark)
    return loader.construct_mapping(node)


_Loader.add_constructor("!reference", _reference)
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@functools.cache
def _files() -> dict[str, dict[str, Any]]:
    """.gitlab-ci.yml and every file it includes, by path."""
    paths = [".gitlab-ci.yml", *sorted(f"ci/{path.name}" for path in (REPO / "ci").glob("*.yml"))]
    return {path: yaml.load((REPO / path).read_text(encoding="utf-8"), Loader=_Loader) for path in paths}


def _jobs() -> dict[str, dict[str, Any]]:
    """Every job and hidden job, as written."""
    return {name: job for config in _files().values() for name, job in config.items() if name not in GLOBAL_KEYS}


def _parents(job: dict[str, Any]) -> list[str]:
    extends = job.get("extends", [])
    return [extends] if isinstance(extends, str) else extends


def _resolve(name: str) -> dict[str, Any]:
    """A job with its !reference tags and extends resolved, as GitLab resolves them."""
    merged: dict[str, Any] = {}
    for parent in _parents(_jobs()[name]):
        merged = _merge(merged, _resolve(parent))
    return _merge(merged, _dereference(_jobs()[name]))


def _dereference(value: Any) -> Any:
    if isinstance(value, Reference):
        node: Any = _jobs()
        for key in value.path:
            node = node[key]
        return _dereference(node)
    if isinstance(value, dict):
        return {key: _dereference(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dereference(item) for item in value]
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _references(value: Any) -> Iterator[Reference]:
    if isinstance(value, Reference):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def _flatten(rules: list[Any]) -> Iterator[dict[str, Any]]:
    """Rules as GitLab reads them: a !reference to a list of rules adds those rules."""
    for rule in rules:
        if isinstance(rule, list):
            yield from _flatten(rule)
        else:
            yield rule


def _rules(job: str) -> list[dict[str, Any]]:
    return list(_flatten(_resolve(job).get("rules", [{}])))


def _commands(job: dict[str, Any]) -> Iterator[str]:
    for key in ("before_script", "script", "after_script"):
        yield from (line for line in job.get(key, []) if isinstance(line, str))


def _script_lines() -> Iterator[str]:
    for job in _jobs().values():
        yield from _commands(job)


def _visible_jobs() -> list[str]:
    return [name for name in _jobs() if not name.startswith(".")]


def _first_rule(rules: list[dict[str, Any]], pipeline: str, changed: str | None) -> dict[str, Any] | None:
    """The rule GitLab applies: the first whose if: and changes: hold.

    changed is the one path a merge request or push changes. Tag and schedule
    pipelines have nothing to compare, and GitLab takes every changes: in them
    to hold, as it does here when changed is None.
    """
    variables = {"CI_DEFAULT_BRANCH": "main", **PIPELINES[pipeline]}
    compared = changed if pipeline in COMPARED else None
    for rule in rules:
        if "if" in rule and not _holds(rule["if"], variables):
            continue
        if compared and "changes" in rule and not any(PurePosixPath(compared).full_match(p) for p in rule["changes"]):
            continue
        return rule
    return None


def _holds(condition: str, variables: dict[str, str]) -> bool:
    """Evaluate an if: in the forms the pipeline uses: variables, == comparisons, && and ||."""
    python = re.sub(r"\$(\w+)", lambda match: repr(variables.get(match[1], "")), condition)
    return bool(eval(python.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}))


def _when(job: str, pipeline: str, changed: str | None = None) -> str:
    """How job starts in a pipeline of that kind: on_success, manual or never."""
    rule = _first_rule(_rules(job), pipeline, changed)
    return "never" if rule is None else str(rule.get("when", "on_success"))


def _pipeline_runs(pipeline: str) -> bool:
    rule = _first_rule(_files()[".gitlab-ci.yml"]["workflow"]["rules"], pipeline, None)
    return rule is not None and rule.get("when") != "never"


def _definition(name: str) -> set[str]:
    """The files that define a job.

    They are .gitlab-ci.yml, which sets the defaults, the files of the job and
    of each template it extends or references, and the ci/ files its commands
    name.
    """
    owners = {job: path for path, config in _files().items() for job in config.keys() - GLOBAL_KEYS}
    jobs: set[str] = set()
    pending = [name]
    while pending:
        job = pending.pop()
        if job not in jobs:
            jobs.add(job)
            pending += _parents(_jobs()[job])
            pending += [reference.path[0] for reference in _references(_jobs()[job])]
    named = re.findall(r"\bci/[\w.-]+", " ".join(_commands(_resolve(name))))
    return {".gitlab-ci.yml", *(owners[job] for job in jobs), *named}


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
    """A path that a changes: pattern matches."""
    return pattern.replace("**", "x").replace("*", "x")


def _pins() -> dict[str, str]:
    text = (REPO / "packaging" / "versions.env").read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", text, re.MULTILINE))


def _image_version(job: str, repository: str) -> str:
    image = _resolve(job)["image"]
    name = image["name"] if isinstance(image, dict) else image
    match = re.fullmatch(rf"{re.escape(repository)}:(\d+\.\d+\.\d+)-[\w.-]+", name)
    assert match, f"{job} runs {name}, not a versioned {repository} image"
    return match[1]


def test_every_ci_file_is_included() -> None:
    included = [entry["local"] for entry in _files()[".gitlab-ci.yml"]["include"]]
    assert sorted(included) == sorted(path for path in _files() if path != ".gitlab-ci.yml")


def test_no_job_is_defined_twice() -> None:
    owner: dict[str, str] = {}
    for path, config in _files().items():
        for name in config.keys() - GLOBAL_KEYS:
            assert name not in owner, f"{name} is defined in {owner[name]} and in {path}"
            owner[name] = path


def test_extends_needs_and_references_name_defined_jobs() -> None:
    jobs = _jobs()
    for name, job in jobs.items():
        for parent in _parents(job):
            assert parent in jobs, f"{name} extends the undefined {parent}"
        for need in [*job.get("needs", []), *job.get("dependencies", [])]:
            target = need if isinstance(need, str) else need["job"]
            assert target in jobs and not target.startswith("."), f"{name} needs the undefined job {target}"
    for reference in _references(_files()):
        node: Any = jobs
        for key in reference.path:
            assert isinstance(node, dict) and key in node, f"!reference {list(reference.path)} names nothing"
            node = node[key]


def test_jobs_run_in_declared_stages() -> None:
    stages = {*_files()[".gitlab-ci.yml"]["stages"], ".pre", ".post"}
    for name in _visible_jobs():
        assert _resolve(name).get("stage", "test") in stages, f"{name} runs in an undeclared stage"


def test_marker_expressions_name_registered_markers() -> None:
    ini = configparser.ConfigParser()
    ini.read(REPO / "pytest.ini", encoding="utf-8")
    markers = {line.split(":")[0].strip() for line in ini["pytest"]["markers"].splitlines() if line.strip()}
    expressions = [expression for line in _script_lines() for expression in re.findall(r'-m "([^"]+)"', line)]
    assert expressions
    for expression in expressions:
        unknown = set(re.findall(r"\w+", expression)) - {"and", "or", "not"} - markers
        assert not unknown, f"-m {expression!r} names unregistered markers {sorted(unknown)}"


def test_pre_commit_skips_name_hooks() -> None:
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}
    skipped = set(_resolve("lint:repo")["variables"]["SKIP"].split(","))
    assert skipped <= hooks, f"SKIP names no hook: {sorted(skipped - hooks)}"


def test_toolchain_images_match_their_pins() -> None:
    toolchain = (REPO / "desktop" / "src-tauri" / "rust-toolchain.toml").read_text(encoding="utf-8")
    assert _image_version(".uv", "ghcr.io/astral-sh/uv") == _pins()["UV_VERSION"]
    assert f'channel = "{_image_version(".rust", "rust")}"' in toolchain
    node = _image_version(".node", "node")
    assert node == str(_resolve(".windows")["variables"]["NODE_VERSION"])
    assert node.split(".")[0] == (REPO / "desktop" / ".nvmrc").read_text(encoding="utf-8").strip()


def test_python_lane_runs_the_bundle_interpreters() -> None:
    matrix = _resolve("test:python")["parallel"]["matrix"]
    versions = {version for entry in matrix for version in entry["UV_PYTHON"]}
    assert versions == {_pins()["PYTHON_VERSION"], _pins()["PYTHON_FALLBACK_VERSION"]}


def test_every_download_the_scripts_check_has_a_pinned_sha256() -> None:
    # A script stops at a download whose pin is empty, so an empty pin fails
    # test:typst and every bundle build, wherever they run.
    scripts = "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in ("ci/*.sh", "ci/*.ps1", "packaging/*.sh", "packaging/*.ps1")
        for path in REPO.glob(pattern)
    )
    names = set(re.findall(r"\b[A-Z]+_(?:WINDOWS|LINUX)_SHA256\b", scripts))
    assert names
    for name in sorted(names):
        assert re.fullmatch(r"[0-9a-f]{64}", _pins().get(name, "")), f"packaging/versions.env pins no SHA-256 {name}"


def test_pipelines_run_for_merge_requests_the_default_branch_tags_and_renovate() -> None:
    # A branch gets no pipeline of its own, so an open merge request runs once,
    # and Renovate's is the one schedule: there is no nightly pipeline (O2).
    assert [kind for kind in PIPELINES if _pipeline_runs(kind)] == [
        "merge request",
        "default branch",
        "tag",
        "unprotected tag",
        "renovate",
    ]


def test_the_renovate_schedule_runs_renovate_alone() -> None:
    for job in _visible_jobs():
        assert (_when(job, "renovate") == "never") is (job != "renovate"), job
    assert [kind for kind in PIPELINES if _when("renovate", kind) != "never"] == ["renovate"]
    # The SAST and Secret Detection jobs come from GitLab's templates.
    rule = _first_rule(_files()[".gitlab-ci.yml"]["workflow"]["rules"], "renovate", None)
    assert rule is not None and rule["variables"] == {"SAST_DISABLED": "true", "SECRET_DETECTION_DISABLED": "true"}


def test_a_merge_request_starts_lint_repo_and_the_lanes_of_its_file() -> None:
    for path, lanes in LANES.items():
        started = {job for job in _visible_jobs() if _when(job, "merge request", path) == "on_success"}
        assert started == EVERY_CHANGE | lanes, path


def test_the_default_branch_runs_no_lane_again() -> None:
    # A semi-linear merge puts on main the tree that the merge request
    # pipeline passed (D69).
    for path in LANES:
        started = {job for job in _visible_jobs() if _when(job, "default branch", path) == "on_success"}
        assert started == EVERY_CHANGE, path


def test_a_tag_runs_every_job() -> None:
    for job in _visible_jobs():
        assert _when(job, "tag") == ("never" if job == "renovate" else "on_success"), job


def test_the_heavy_windows_jobs_run_on_tags_and_otherwise_by_hand() -> None:
    assert {job for job in _visible_jobs() if _when(job, "merge request") == "manual"} == HEAVY
    for job in HEAVY:
        assert {_when(job, kind, path) for kind in COMPARED for path in LANES} == {"manual"}, job


def test_manual_jobs_never_block_the_pipeline() -> None:
    for job in _visible_jobs():
        for rule in _rules(job):
            if rule.get("when") == "manual":
                assert rule.get("allow_failure") is True, job


def test_a_change_to_a_job_definition_runs_the_job() -> None:
    for job in _visible_jobs():
        if _when(job, "merge request") != "never":
            for path in _definition(job):
                assert _when(job, "merge request", path) != "never", f"{job} skips a change to {path}"


def test_every_job_a_job_needs_runs_with_it() -> None:
    # GitLab refuses to create a pipeline in which a job needs one that the
    # rules leave out, and a job that needs a manual one waits for a click.
    patterns = {pattern for job in _visible_jobs() for rule in _rules(job) for pattern in rule.get("changes", [])}
    changes = [None, *LANES, *map(_example, patterns)]
    for job in _visible_jobs():
        for need in _resolve(job).get("needs", []):
            target = need if isinstance(need, str) else need["job"]
            for kind in PIPELINES:
                for changed in changes:
                    if _when(job, kind, changed) != "never":
                        assert _when(target, kind, changed) == "on_success", f"{job} needs {target} ({kind}, {changed})"


def test_the_lanes_cover_the_files_their_tests_read() -> None:
    # The UI and the shell name files relative to the source that reads them,
    # the Python tests relative to the repository.
    relative = (
        ("test:ui", "desktop/src", {".ts", ".tsx"}, r"""(?:from|import)\s*\(?\s*["'](\.{1,2}/[^"'?]+)"""),
        ("test:rust", "desktop/src-tauri/src", {".rs"}, r'include_(?:str|bytes)!\(\s*"([^"]+)"'),
    )
    reads = {
        job: {
            (path.parent / target).resolve().relative_to(REPO).as_posix()
            for path in (REPO / folder).rglob("*")
            if path.suffix in suffixes
            for target in re.findall(pattern, path.read_text(encoding="utf-8"))
        }
        for job, folder, suffixes, pattern in relative
    }
    reads["test:python"] = {
        "/".join(re.findall(r'"([^"]+)"', chain))
        for path in [*(REPO / "tests").glob("*.py"), *(REPO / "scripts").glob("test_*.py")]
        for chain in re.findall(r'REPO((?: / "[^"]+")+)', path.read_text(encoding="utf-8"))
    }
    for job, paths in reads.items():
        assert any(path.startswith("tests/fixtures/") for path in paths), job
        changes = {f"{path}/x" if (REPO / path).is_dir() else path for path in paths}
        for changed in sorted(changes - _ignored(changes)):
            assert _when(job, "merge request", changed) == "on_success", f"{job} skips a change to {changed}"


def test_ruff_checks_every_python_file_a_merge_request_changes() -> None:
    commands = {"uv run ruff check", "uv run ruff format --check"}
    ruff = [job for job in _visible_jobs() if commands <= set(_commands(_resolve(job)))]
    excluded = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]["extend-exclude"]
    checked = [path for path in _tracked("*.py") if not any(PurePosixPath(path).is_relative_to(x) for x in excluded)]
    assert "ci/test_pipeline.py" in checked
    for path in checked:
        assert any(_when(job, "merge request", path) == "on_success" for job in ruff), path


def test_prettier_checks_every_desktop_file_a_merge_request_changes() -> None:
    # lint:ui runs `prettier --check .` in desktop/, which reaches the shell's
    # JSON configuration too.
    formatted = [path for path in _tracked("desktop") if PurePosixPath(path).suffix in PRETTIER]
    assert "desktop/src-tauri/tauri.conf.json" in formatted
    for path in formatted:
        assert _when("lint:ui", "merge request", path) == "on_success", path


def test_the_windows_fast_lane_runs_pytest_in_a_short_folder() -> None:
    # The worker refuses a project path longer than 114 characters while
    # Windows long paths are off, and a test project under the runner
    # profile's temp folder can pass that. pytest creates --basetemp but not
    # its parent, so the folder sits at the drive root.
    (pytest,) = [line for line in _resolve("test:windows")["script"] if line.startswith("uv run pytest ")]
    assert re.search(r" --basetemp=[A-Z]:\\\w+ ", pytest)


def test_the_ui_lane_reports_its_coverage() -> None:
    job = _resolve("test:ui")
    (command,) = job["script"]
    folder = re.search(r'--coverage\.reportsDirectory="\$CI_PROJECT_DIR/([^"]+)"', command)
    assert command.startswith("npm run test:ci -- ") and folder
    assert job["artifacts"]["reports"]["coverage_report"] == {
        "coverage_format": "cobertura",
        "path": f"{folder[1]}/cobertura-coverage.xml",
    }
    # The total line of vitest's text coverage report.
    total = re.search(job["coverage"].strip("/"), "All files          |   84.31 |    80.12 |   88.88 |   84.31 |")
    assert total and total[1] == "84.31"


def test_git_ignores_what_the_jobs_leave_in_the_checkout() -> None:
    # The folders that the CI variables and the Windows tool scripts name and
    # the cache paths, each with a file in it, and the artifacts and test
    # reports the jobs write.
    sources = [*(REPO / path for path in _files()), *(REPO / "ci").glob("*.ps1")]
    text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    folders = {
        *re.findall(r"\$CI_PROJECT_DIR/([^\s\"'/:;]+)", text),
        *re.findall(r'Join-Path \$Root "(\.[^"]+)"', text),
    }
    outputs: set[str] = set()
    for job in map(_resolve, _visible_jobs()):
        cache = job.get("cache", [])
        for entry in [cache] if isinstance(cache, dict) else cache:
            folders.update(entry["paths"])
        artifacts = job.get("artifacts", {})
        reports = artifacts.get("reports", {})
        outputs.update(artifacts.get("paths", []))
        outputs.update(path for path in (reports.get("junit"), reports.get("coverage_report", {}).get("path")) if path)
    # pytest --cov and `npm run test:ci` write these in any checkout: the
    # coverage data, with a file per measured process until coverage.py
    # combines them, vitest's coverage folder, and the JUnit report that
    # vite.config.ts names.
    junit = re.search(r'junit: "([^"]+)"', (REPO / "desktop" / "vite.config.ts").read_text(encoding="utf-8"))
    assert junit
    outputs |= {".coverage", ".coverage.host.123.456", "desktop/coverage/index.html", f"desktop/{junit[1]}"}
    kept = {f"{folder.rstrip('/')}/x" for folder in folders} | outputs
    assert {".tools/x", ".cargo/x", ".uv-cache/x", "reports/junit-repo.xml"} <= kept
    assert sorted(kept - _ignored(kept)) == []


def test_release_notes_go_into_a_folder_the_job_creates() -> None:
    script = _resolve("release:notes")["script"]
    (cliff,) = [index for index, line in enumerate(script) if "git-cliff" in line]
    output = re.search(r"--output (\S+)", script[cliff])
    assert output and f"mkdir -p {PurePosixPath(output[1]).parent}" in script[:cliff]


def test_the_release_is_created_with_glab() -> None:
    # release-cli is deprecated in favour of glab.
    assert _resolve("release:publish")["image"].startswith("registry.gitlab.com/gitlab-org/cli:")


def test_releases_come_from_protected_tags_alone() -> None:
    for job in _visible_jobs():
        if job.startswith("release:"):
            assert [kind for kind in PIPELINES if _when(job, kind) != "never"] == ["tag"], job
