"""The GitLab pipeline in .gitlab-ci.yml and ci/*.yml holds together.

No GitLab project exists yet to lint the pipeline (D65). These checks catch
what GitLab reports only when it creates a pipeline, and what it never
reports: a job defined twice, a pre-commit SKIP or a pytest marker that names
nothing, a CI pin that disagrees with its source, and rules that start a job
in pipelines the decisions keep it out of.
"""

from __future__ import annotations

import configparser
import functools
import re
from collections.abc import Iterator
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
    "nightly": {"CI_PIPELINE_SOURCE": "schedule", "CI_COMMIT_BRANCH": "main"},
    "renovate": {"CI_PIPELINE_SOURCE": "schedule", "CI_COMMIT_BRANCH": "main", "SCHEDULE_KIND": "renovate"},
}
WORKER_CHANGE = "worker/mathimator_worker/session.py"


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


def _resolve(name: str) -> dict[str, Any]:
    """A job with its extends merged in, as GitLab merges them."""
    job = _jobs()[name]
    extends = job.get("extends", [])
    merged: dict[str, Any] = {}
    for parent in [extends] if isinstance(extends, str) else extends:
        merged = _merge(merged, _resolve(parent))
    return _merge(merged, job)


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


def _script_lines() -> Iterator[str]:
    for job in _jobs().values():
        for key in ("before_script", "script", "after_script"):
            yield from (line for line in job.get(key, []) if isinstance(line, str))


def _visible_jobs() -> list[str]:
    return [name for name in _jobs() if not name.startswith(".")]


def _first_rule(rules: list[dict[str, Any]], pipeline: str, changed: str | None) -> dict[str, Any] | None:
    """The rule GitLab applies: the first whose if: and changes: hold.

    changed is the one path a merge request or push changes. Tag and schedule
    pipelines have nothing to compare, and GitLab takes every changes: in them
    to hold, as it does here when changed is None.
    """
    variables = {"CI_DEFAULT_BRANCH": "main", **PIPELINES[pipeline]}
    for rule in rules:
        if "if" in rule and not _holds(rule["if"], variables):
            continue
        if changed and "changes" in rule and not any(PurePosixPath(changed).full_match(p) for p in rule["changes"]):
            continue
        return rule
    return None


def _holds(condition: str, variables: dict[str, str]) -> bool:
    """Evaluate an if: in the forms the pipeline uses: variables, == comparisons, && and ||."""
    python = re.sub(r"\$(\w+)", lambda match: repr(variables.get(match[1], "")), condition)
    return bool(eval(python.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}))


def _when(job: str, pipeline: str, changed: str | None = None) -> str:
    """How job starts in a pipeline of that kind: on_success, manual or never."""
    rule = _first_rule(_resolve(job).get("rules", [{}]), pipeline, changed)
    return "never" if rule is None else str(rule.get("when", "on_success"))


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
        extends = job.get("extends", [])
        for parent in [extends] if isinstance(extends, str) else extends:
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
    for name in _jobs():
        if not name.startswith("."):
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
    skipped = set(_resolve("lint:pre-commit")["variables"]["SKIP"].split(","))
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


def test_pipelines_run_for_merge_requests_the_default_branch_tags_and_schedules() -> None:
    workflow = _files()[".gitlab-ci.yml"]["workflow"]["rules"]
    # A branch gets no pipeline of its own, so an open merge request runs once.
    assert [kind for kind in PIPELINES if _first_rule(workflow, kind, None) is not None] == [
        kind for kind in PIPELINES if kind != "branch"
    ]


def test_the_renovate_schedule_runs_renovate_alone() -> None:
    for job in _visible_jobs():
        assert (_when(job, "renovate") == "never") is (job != "renovate"), job
    assert [kind for kind in PIPELINES if _when("renovate", kind) != "never"] == ["renovate"]
    # The SAST and Secret Detection jobs come from GitLab's templates.
    rule = _first_rule(_files()[".gitlab-ci.yml"]["workflow"]["rules"], "renovate", None)
    assert rule is not None and rule["variables"] == {"SAST_DISABLED": "true", "SECRET_DETECTION_DISABLED": "true"}


def test_manual_jobs_never_block_the_pipeline() -> None:
    for job in _visible_jobs():
        for rule in _resolve(job).get("rules", []):
            if rule.get("when") == "manual":
                assert rule.get("allow_failure") is True, job


def test_windows_lanes_run_for_the_worker_and_the_shell_and_on_tags_and_schedules() -> None:
    for job in ("test:windows", "test:rust:windows"):
        for kind in ("merge request", "default branch"):
            assert _when(job, kind, WORKER_CHANGE) == "on_success", job
            assert _when(job, kind, "desktop/src-tauri/src/lib.rs") == "on_success", job
            assert _when(job, kind, "docs/design-spec.md") == "never", job
        assert _when(job, "tag") == _when(job, "nightly") == "on_success", job


def test_the_render_lane_runs_nightly_and_on_tags_and_on_request_in_merge_requests() -> None:
    job = "test:render:windows"
    assert _when(job, "tag") == _when(job, "nightly") == "on_success"
    assert _when(job, "merge request", WORKER_CHANGE) == "manual"
    assert _when(job, "default branch", WORKER_CHANGE) == "never"


def test_the_installer_is_built_nightly_and_on_tags_and_on_demand() -> None:
    job = "package:windows"
    assert _when(job, "tag") == _when(job, "nightly") == "on_success"
    assert _when(job, "merge request", WORKER_CHANGE) == _when(job, "default branch", WORKER_CHANGE) == "manual"


def test_releases_come_from_protected_tags_alone() -> None:
    for job in _visible_jobs():
        if job.startswith("release:"):
            assert [kind for kind in PIPELINES if _when(job, kind) != "never"] == ["tag"], job
