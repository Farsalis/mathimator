"""scripts/check_versions.py: the app version and the toolchain pins agree everywhere."""

from __future__ import annotations

import json
from pathlib import Path

import check_versions

PINS = """\
UV_VERSION=0.13.5
PYTHON_VERSION=3.14.9
PYTHON_FALLBACK_VERSION=3.13.17
TYPST_VERSION=0.15.0
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(tmp_path: Path, version: str = "0.1.0") -> Path:
    """A checkout whose versions and pins all agree."""
    repo = tmp_path / "repo"
    tauri = "desktop/src-tauri"
    files = {
        ".bumpversion.toml": f'[tool.bumpversion]\ncurrent_version = "{version}"\n',
        ".python-version": "3.14.9\n",
        "packaging/versions.env": PINS,
        "pyproject.toml": (
            '[project]\nname = "mathimator-dev"\nversion = "0.0.0"\n\n'
            '[dependency-groups]\nrender = ["manim[typst]==0.22.0"]\n\n'
            '[tool.uv]\nrequired-version = "==0.13.5"\nconstraint-dependencies = ["av==19.0.2", "manim==0.22.0"]\n'
        ),
        "uv.lock": (
            f'[[package]]\nname = "mathimator-dev"\nversion = "0.0.0"\n\n'
            f'[[package]]\nname = "mathimator-worker"\nversion = "{version}"\n'
        ),
        "worker/pyproject.toml": f'[project]\nname = "mathimator-worker"\nversion = "{version}"\n',
        "worker/mathimator_worker/__init__.py": f'"""The worker."""\n\n__version__ = "{version}"\n',
        "worker/mathimator_worker/document.py": 'MANIM_VERSION = "0.22.0"\n',
        "desktop/.nvmrc": "24\n",
        "desktop/package.json": json.dumps({"name": "mathimator", "version": version, "engines": {"node": ">=24 <25"}}),
        "desktop/package-lock.json": json.dumps(
            {"name": "mathimator", "version": version, "packages": {"": {"name": "mathimator", "version": version}}}
        ),
        f"{tauri}/Cargo.toml": f'[package]\nname = "mathimator"\nversion = "{version}"\nrust-version = "1.93"\n',
        f"{tauri}/Cargo.lock": f'[[package]]\nname = "serde"\nversion = "1.0.0"\n\n'
        f'[[package]]\nname = "mathimator"\nversion = "{version}"\n',
        f"{tauri}/tauri.conf.json": json.dumps({"productName": "Mathimator", "version": version}),
        f"{tauri}/rust-toolchain.toml": '[toolchain]\nchannel = "1.93.0"\n',
        f"{tauri}/src/equation.rs": 'if !line.starts_with("typst 0.15.0") {\n    return Err("Typst 0.15.0 only");\n}\n',
    }
    for name, text in files.items():
        write(repo / name, text)
    return repo


def test_this_checkout_agrees() -> None:
    versions = check_versions.app_versions(check_versions.REPO)

    assert check_versions.version_problems(versions, None) == []
    assert check_versions.pin_problems(check_versions.REPO) == []


def test_the_app_version_is_read_from_every_file_that_records_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "1.2.3")

    versions = check_versions.app_versions(repo)
    assert versions == {
        ".bumpversion.toml": "1.2.3",
        "worker/pyproject.toml": "1.2.3",
        "worker/mathimator_worker/__init__.py": "1.2.3",
        "uv.lock": "1.2.3",
        "desktop/package.json": "1.2.3",
        "desktop/package-lock.json": "1.2.3",
        'desktop/package-lock.json packages[""]': "1.2.3",
        "desktop/src-tauri/Cargo.toml": "1.2.3",
        "desktop/src-tauri/Cargo.lock": "1.2.3",
        "desktop/src-tauri/tauri.conf.json": "1.2.3",
    }
    assert check_versions.version_problems(versions, None) == []
    assert check_versions.pin_problems(repo) == []


def test_a_file_left_behind_by_a_bump_is_reported(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo / "desktop" / "package.json", json.dumps({"version": "0.2.0", "engines": {"node": ">=24 <25"}}))

    problems = check_versions.version_problems(check_versions.app_versions(repo), None)
    assert problems[0] == "the app version differs between files:"
    assert "  desktop/package.json: 0.2.0" in problems
    assert "  desktop/src-tauri/Cargo.toml: 0.1.0" in problems


def test_tauri_conf_may_leave_the_version_to_cargo(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo / "desktop" / "src-tauri" / "tauri.conf.json", json.dumps({"productName": "Mathimator"}))

    assert "desktop/src-tauri/tauri.conf.json" not in check_versions.app_versions(repo)


def test_a_release_tag_must_be_v_and_the_app_version() -> None:
    versions = {"desktop/src-tauri/Cargo.toml": "0.1.0", "desktop/package.json": "0.1.0"}

    assert check_versions.version_problems(versions, "v0.1.0") == []
    assert check_versions.version_problems(versions, "0.1.0") == [
        "tag 0.1.0 does not match the app version 0.1.0: expected v0.1.0"
    ]
    assert check_versions.version_problems(versions, "v0.1.0-rc.1") == [
        "tag v0.1.0-rc.1 does not match the app version 0.1.0: expected v0.1.0"
    ]


def test_each_toolchain_pin_must_match_its_source(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    tauri = repo / "desktop" / "src-tauri"
    write(tauri / "rust-toolchain.toml", '[toolchain]\nchannel = "1.94.0"\n')
    write(repo / "desktop" / ".nvmrc", "22\n")
    write(repo / ".python-version", "3.14.8\n")
    write(repo / "worker" / "mathimator_worker" / "document.py", 'MANIM_VERSION = "0.23.0"\n')
    write(tauri / "src" / "equation.rs", 'if !line.starts_with("typst 0.14.2") {}\n')
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")
    write(repo / "pyproject.toml", pyproject.replace("==0.13.5", "==0.13.4"))

    assert check_versions.pin_problems(repo) == [
        "rust-toolchain.toml pins Rust 1.94.0, but Cargo.toml rust-version is 1.93",
        "desktop/.nvmrc pins Node 22, but package.json engines.node is '>=24 <25'",
        ".python-version is 3.14.8, but packaging/versions.env pins 3.14.9",
        "pyproject.toml requires uv ==0.13.4, but packaging/versions.env pins 0.13.5",
        "the pyproject.toml render group does not pin manim[typst]==0.23.0 (document.py)",
        "the pyproject.toml constraint-dependencies do not pin manim==0.23.0 (document.py)",
        "equation.rs requires Typst 0.14.2, but packaging/versions.env pins 0.15.0",
    ]


def test_only_the_typst_guard_in_equation_rs_is_the_pin(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    src = repo / "desktop" / "src-tauri" / "src"
    write(src / "runtime.rs", 'const FIXTURE: &str = r#"{"typstVersion": "typst 0.14.2"}"#;\n')

    assert check_versions.pin_problems(repo) == []
    write(src / "equation.rs", 'return Err("Typst 0.15.0 is required");\n')
    assert check_versions.pin_problems(repo) == [
        'equation.rs has no starts_with("typst X.Y.Z") guard or VERSION constant for Typst'
    ]


def test_the_typst_guard_may_compare_with_a_constant(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    equation = repo / "desktop" / "src-tauri" / "src" / "equation.rs"
    guard = 'if line.starts_with(VERSION) {}\n\n#[test]\nfn rejects() { echo("typst 0.14.2"); }\n'

    write(equation, f'const VERSION: &str = "typst 0.15.0";\n{guard}')
    assert check_versions.pin_problems(repo) == []
    write(equation, f'const VERSION: &str = "typst 0.14.2";\n{guard}')
    assert check_versions.pin_problems(repo) == [
        "equation.rs requires Typst 0.14.2, but packaging/versions.env pins 0.15.0"
    ]


def test_rust_version_may_name_the_minor_release(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    cargo = repo / "desktop" / "src-tauri" / "Cargo.toml"

    for rust_version, agrees in (("1.93", True), ("1.93.0", True), ("1.9", False), ("1.93.1", False)):
        write(cargo, f'[package]\nname = "mathimator"\nversion = "0.1.0"\nrust-version = "{rust_version}"\n')
        assert (check_versions.pin_problems(repo) == []) is agrees, rust_version
