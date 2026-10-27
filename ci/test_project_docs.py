"""The project documents keep up with the code they describe.

The environment variable table in CONTRIBUTING.md has a row for each
MATHIMATOR_* variable the code reads or sets and for no other, the npm
scripts that README.md and CONTRIBUTING.md name exist, and every rule in
.github/CODEOWNERS matches a file and names an owner.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# The code that reads or sets MATHIMATOR_* variables: the worker, the UI and
# its Vite config, the shell and its build script, the tests with their C#
# stand-ins, the scripts, the bundle builds, and the CI workflows and their
# scripts. This file is left out: it names TEST_PREFIX.
SOURCES = [
    "worker/mathimator_worker/**/*.py",
    "desktop/*.ts",
    "desktop/src/**/*.ts",
    "desktop/src/**/*.tsx",
    "desktop/src-tauri/build.rs",
    "desktop/src-tauri/src/**/*.rs",
    "tests/**/*.py",
    "tests/fixtures/win32/*.cs",
    "scripts/*.py",
    "packaging/*.ps1",
    "packaging/*.sh",
    ".github/workflows/*.yml",
    "ci/*.ps1",
    "ci/*.sh",
]
# The Win32 tests pass these to their stand-ins; CONTRIBUTING.md lists them as one row.
TEST_PREFIX = "MATHIMATOR_TEST_"


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_contributing_lists_exactly_the_environment_variables() -> None:
    names = {
        name
        for pattern in SOURCES
        for path in REPO.glob(pattern)
        for name in re.findall(r"MATHIMATOR_[A-Z0-9_]+", path.read_text(encoding="utf-8"))
    }
    in_code = {f"{TEST_PREFIX}*" if name.startswith(TEST_PREFIX) else name for name in names}
    # The table rows only: a variable named in prose does not count as listed.
    rows = set(re.findall(r"^\| `(MATHIMATOR_[A-Z0-9_*]+)`", _read("CONTRIBUTING.md"), re.MULTILINE))
    assert rows == in_code


def test_documented_npm_scripts_exist() -> None:
    scripts = json.loads(_read("desktop/package.json"))["scripts"]
    named = {
        match[1] or "test"
        for document in ("README.md", "CONTRIBUTING.md")
        for match in re.finditer(r"\bnpm (?:run ([\w:-]+)|test\b)", _read(document))
    }
    assert sorted(named - scripts.keys()) == []


def test_codeowners_rules_match_files_and_name_an_owner() -> None:
    # GitHub's syntax: a pattern and its owners on each line, and no sections.
    rules = [line.split() for line in _read(".github/CODEOWNERS").splitlines() if line and not line.startswith("#")]
    assert rules[0] == ["*", "@Farsalis"]
    for pattern, *owners in rules:
        assert any(REPO.glob(pattern.strip("/"))), f"{pattern} matches no file"
        assert owners and all(owner.startswith("@") for owner in owners), f"{pattern} names no owner"
