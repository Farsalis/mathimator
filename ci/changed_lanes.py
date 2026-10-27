"""Pick the CI lanes that a pull request needs.

    git diff --name-only --no-renames -z BASE HEAD | python3 ci/changed_lanes.py
    python3 ci/changed_lanes.py --all

The changes job in .github/workflows/ci.yml runs it and hands its output, one
lane=true or lane=false line per lane, to the other jobs through
GITHUB_OUTPUT. A lane runs when the change touches a file that its jobs test,
lint or read. A change to the workflow or to this script runs every lane, and
so does every event other than a pull request (--all). The checks that span
the lanes, such as Ruff, mypy and the ci/ tests, have no lane: they run every
time.

It reads the NUL-separated paths that `git diff -z` writes, needs only the
standard library, and runs on the Python of the runner image.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable

# The patterns use git's glob syntax: * stays within a folder, ** crosses
# folders, and **/ also matches no folder at all.
_PYTHON_SOURCES = (
    "worker/**",
    "tests/**",
    "pyproject.toml",
    "uv.lock",
    "pytest.ini",
    ".python-version",
)
# The worker, its tests and golden scenes, the spec listings they compare, the
# scripts and the packaging files their tests read, and the Python
# dependencies and pins.
_PYTHON = (*_PYTHON_SOURCES, "scenes/**", "docs/design-spec.md", "scripts/**", "packaging/**")
# The shell, and the fixtures that its tests embed.
_RUST = (
    "desktop/src-tauri/**",
    "tests/fixtures/equation/**",
    "tests/fixtures/protocol/**",
    "tests/fixtures/win32/**",
)

LANES: dict[str, tuple[str, ...]] = {
    "python": _PYTHON,
    # The worker and its tests, the Typst pin and the script that installs it.
    "typst": (*_PYTHON_SOURCES, "packaging/versions.env", "ci/install-typst.sh"),
    # The Python lane on Windows, which also runs the ci/ tests, and the
    # script that installs its tools.
    "windows": (*_PYTHON, "ci/*.py", "ci/windows-tools.ps1"),
    # The UI, the shell's JSON configuration that Prettier checks, and the
    # fixtures and worker source that the UI tests import.
    "ui": (
        "desktop/*",
        "desktop/src/**",
        "desktop/src-tauri/**/*.json",
        "tests/fixtures/beats/**",
        "tests/fixtures/protocol/**",
        "tests/fixtures/render-smoke/**",
        "tests/fixtures/timing/**",
        "worker/mathimator_worker/supervisor.py",
    ),
    "rust": _RUST,
    # On Windows the shell's ignored tests also start the repository's worker
    # with the pinned Python.
    "rust_windows": (*_RUST, "worker/**", ".python-version", "ci/windows-tools.ps1"),
    # Each audit: its lockfile and manifest, and the cargo-deny policy.
    "audit_python": ("pyproject.toml", "uv.lock", "worker/pyproject.toml"),
    "audit_npm": ("desktop/package.json", "desktop/package-lock.json"),
    "audit_rust": ("desktop/src-tauri/Cargo.toml", "desktop/src-tauri/Cargo.lock", "ci/deny.toml"),
}
# A change to these runs every lane.
EVERY_LANE = (".github/workflows/ci.yml", "ci/changed_lanes.py")


def _regex(pattern: str) -> re.Pattern[str]:
    parts = re.split(r"(\*\*/|\*\*|\*)", pattern)
    wildcards = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}
    return re.compile("".join(wildcards.get(part, re.escape(part)) for part in parts))


def matches(path: str, patterns: Iterable[str]) -> bool:
    """Whether a repository path matches one of the patterns."""
    return any(_regex(pattern).fullmatch(path) for pattern in patterns)


def lanes(paths: Iterable[str]) -> dict[str, bool]:
    """Whether each lane runs for a change to these paths."""
    changed = [path for path in paths if path]
    every = any(matches(path, EVERY_LANE) for path in changed)
    return {lane: every or any(matches(path, patterns) for path in changed) for lane, patterns in LANES.items()}


def main(argv: list[str]) -> int:
    if argv == ["--all"]:
        selected = dict.fromkeys(LANES, True)
    elif not argv:
        selected = lanes(sys.stdin.buffer.read().decode("utf-8", "surrogateescape").split("\0"))
    else:
        print("usage: changed_lanes.py [--all] < NUL-separated paths", file=sys.stderr)
        return 2
    for lane, runs in selected.items():
        print(f"{lane}={str(runs).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
