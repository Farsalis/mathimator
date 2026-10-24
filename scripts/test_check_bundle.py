"""scripts/check_bundle.py: the check before `tauri build`, and the build scripts' prune."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
from pathlib import Path
from unittest import mock

import check_bundle

PINS = """\
# Comments and blank lines are skipped.

UV_VERSION=0.13.5
PYTHON_VERSION=3.14.9
PYTHON_FALLBACK_VERSION=3.13.17
TINYTEX_VERSION=2026.10
TLNET_REPOSITORY=https://texlive.info/tlnet-archive/2026/10/23/tlnet
TYPST_VERSION=0.15.0
"""
PLATFORM = "windows-x86_64"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(tmp_path: Path) -> Path:
    """A checkout holding the files the check compares a bundle with."""
    repo = tmp_path / "repo"
    files = {
        "worker/mathimator_worker/__init__.py": '__version__ = "0.1.0"\n',
        "worker/mathimator_worker/supervisor.py": "SUPERVISOR = 1\n",
        "worker/mathimator_worker/__pycache__/supervisor.cpython-314.pyc": "bytecode",
        "tests/fixtures/reference-60/project.json": '{"schemaVersion": 1}\n',
        "uv.lock": "version = 1\n",
        "desktop/src-tauri/Cargo.lock": "version = 4\n",
        "desktop/package-lock.json": "{}\n",
        "packaging/versions.env": PINS,
    }
    for name, text in files.items():
        write(repo / name, text)
    return repo


def notices_for(repo: Path) -> str:
    return "".join(f"  {lock} SHA-256 {sha256(repo / lock)}\n" for lock in check_bundle.LOCKS)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_bundle(tmp_path: Path, repo: Path) -> Path:
    """A bundle built from repo that passes the check."""
    bundle = tmp_path / "bundle"
    for name in ("python/python.exe", "typst/typst.exe", "tinytex/bin/windows/latex.exe", ".gitkeep"):
        write(bundle / name, "binary")
    shutil.copytree(
        repo / "worker" / "mathimator_worker",
        bundle / "worker" / "mathimator_worker",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    sample = bundle / "samples" / "reference-60" / "project.json"
    write(sample, (repo / "tests" / "fixtures" / "reference-60" / "project.json").read_text(encoding="utf-8"))
    write_runtime(bundle, sha256(repo / "uv.lock"))
    write(bundle / check_bundle.NOTICES, notices_for(repo))
    return bundle


def write_runtime(bundle: Path, lock_sha256: str, **changes: object) -> None:
    runtime: dict[str, object] = {
        "python": "python/python.exe",
        "worker": "worker",
        "pythonVersion": "3.14.9",
        "pythonSelection": "CPython 3.14.9",
        "manim": "0.22.0",
        "lockSha256": lock_sha256,
        "typst": "typst/typst.exe",
        "typstVersion": "typst 0.15.0 (3ae52774)",
        "tinytexBin": "tinytex/bin/windows",
        "tinytexVersion": "tlmgr revision 80658",
        "tinytexRelease": "2026.10",
        "tlnetRepository": "https://texlive.info/tlnet-archive/2026/10/23/tlnet",
        "sample": "samples/reference-60/project.json",
        "platform": PLATFORM,
    }
    runtime.update(changes)
    write(bundle / "runtime.json", json.dumps({key: value for key, value in runtime.items() if value is not None}))


def test_a_bundle_built_from_the_checkout_passes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)

    assert check_bundle.verify(bundle, repo, PLATFORM) == []


def test_runtime_json_must_exist_and_be_an_object(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)

    (bundle / "runtime.json").unlink()
    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "runtime.json is missing: build the bundle with packaging/build-bundle.ps1 or .sh"
    ]
    write(bundle / "runtime.json", "[]")
    assert check_bundle.verify(bundle, repo, PLATFORM) == ["runtime.json is not a JSON object"]
    write(bundle / "runtime.json", "{")
    assert check_bundle.verify(bundle, repo, PLATFORM)[0].startswith("runtime.json is unreadable: ")


def test_every_field_the_installer_needs_is_required(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)

    for field in check_bundle.FIELDS:
        write_runtime(bundle, sha256(repo / "uv.lock"), **{field: None})
        assert check_bundle.verify(bundle, repo, PLATFORM) == [f"runtime.json has no {field}"]


def test_the_platform_must_be_this_machines(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)

    assert check_bundle.verify(bundle, repo, "linux-x86_64") == ["runtime.json is for windows-x86_64, not linux-x86_64"]
    assert re.fullmatch(r"[a-z0-9]+-[a-z0-9_]+", check_bundle.host_platform())


def test_paths_must_stay_inside_the_bundle_and_exist(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)
    lock = sha256(repo / "uv.lock")

    for outside in ("../python.exe", "/usr/bin/python3", "C:\\Python\\python.exe", "python\\..\\..\\x.exe"):
        write_runtime(bundle, lock, python=outside)
        assert check_bundle.verify(bundle, repo, PLATFORM) == [
            f"runtime.json python {outside!r} is not a relative path inside the bundle"
        ]
    write_runtime(bundle, lock, typst="typst/missing.exe", tinytexBin="tinytex/bin/windows/latex.exe")
    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "runtime.json typst typst/missing.exe is not in the bundle",
        "runtime.json tinytexBin tinytex/bin/windows/latex.exe is not in the bundle",
    ]


def test_a_venv_launcher_is_not_the_real_interpreter(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)
    write(bundle / "python" / "Scripts" / "python.exe", "launcher")
    write(bundle / "python" / "pyvenv.cfg", "home = C:\\Python314\n")

    write_runtime(bundle, sha256(repo / "uv.lock"), python="python/Scripts/python.exe")
    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "python/Scripts/python.exe is a venv launcher, not the real interpreter (D33)"
    ]


def test_the_worker_and_sample_must_match_the_checkout(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)
    package = bundle / "worker" / "mathimator_worker"
    write(package / "supervisor.py", "SUPERVISOR = 0\n")
    (package / "__init__.py").unlink()
    write(package / "old_module.py", "")
    write(bundle / "worker" / "README.txt", "")
    write(bundle / "samples" / "reference-60" / "project.json", "{}\n")

    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "bundled worker is stale: README.txt is not in the sources",
        "bundled worker is stale: mathimator_worker/__init__.py is missing",
        "bundled worker is stale: mathimator_worker/old_module.py is not in the sources",
        "bundled worker is stale: mathimator_worker/supervisor.py differs",
        "bundled sample samples/reference-60/project.json differs from tests/fixtures/reference-60/project.json",
    ]


def test_the_bundle_must_come_from_this_checkouts_lock_and_pins(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)
    lock = sha256(repo / "uv.lock")

    write_runtime(bundle, lock, pythonVersion="3.13.17")
    assert check_bundle.verify(bundle, repo, PLATFORM) == []
    write_runtime(
        bundle,
        "0" * 64,
        pythonVersion="3.14.8",
        typstVersion="typst 0.14.0 (b2c8e1a5)",
        tinytexRelease="2026.04",
        tlnetRepository="https://mirror.ctan.org/systems/texlive/tlnet",
    )
    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "runtime.json lockSha256 is not the SHA-256 of uv.lock: rebuild the bundle",
        "runtime.json pythonVersion is '3.14.8', but packaging/versions.env pins 3.14.9 or 3.13.17",
        "runtime.json typstVersion is 'typst 0.14.0 (b2c8e1a5)', but packaging/versions.env pins typst 0.15.0",
        "runtime.json tinytexRelease is '2026.04', but packaging/versions.env pins 2026.10",
        "runtime.json tlnetRepository is 'https://mirror.ctan.org/systems/texlive/tlnet', but "
        "packaging/versions.env pins https://texlive.info/tlnet-archive/2026/10/23/tlnet",
    ]


def test_the_notices_must_name_the_current_lockfiles(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)

    write(repo / "desktop" / "src-tauri" / "Cargo.lock", "version = 4\n# a crate was bumped\n")
    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "THIRD_PARTY_NOTICES.txt predates desktop/src-tauri/Cargo.lock: run scripts/generate_notices.py"
    ]
    (bundle / check_bundle.NOTICES).unlink()
    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "THIRD_PARTY_NOTICES.txt is missing: run scripts/generate_notices.py"
    ]


def test_unexpected_entries_and_bytecode_fail_until_pruned(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)
    write(bundle / "crash.dmp", "dump")
    write(bundle / "venv" / "pyvenv.cfg", "")
    write(bundle / "worker" / "mathimator_worker" / "__pycache__" / "supervisor.cpython-314.pyc", "")
    write(bundle / "python" / "Lib" / "__pycache__" / "os.cpython-314.pyc", "")

    assert check_bundle.verify(bundle, repo, PLATFORM) == [
        "unexpected bundle entry crash.dmp (run with --prune)",
        "unexpected bundle entry venv (run with --prune)",
        "2 __pycache__ folders in the bundle (run with --prune)",
    ]
    assert (bundle / "crash.dmp").exists()

    check_bundle.prune(bundle)
    assert sorted(entry.name for entry in bundle.iterdir()) == sorted(check_bundle.LAYOUT)
    assert not list(bundle.rglob("__pycache__"))
    assert check_bundle.verify(bundle, repo, PLATFORM) == []


def test_prune_cleans_a_bundle_that_has_no_runtime_json_or_notices_yet(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, make_repo(tmp_path))
    (bundle / "runtime.json").unlink()
    (bundle / check_bundle.NOTICES).unlink()
    write(bundle / "crash.dmp", "dump")
    write(bundle / "python" / "Lib" / "__pycache__" / "os.cpython-314.pyc", "")

    with contextlib.redirect_stdout(io.StringIO()):
        assert check_bundle.main(["--prune", str(bundle)]) == 0
    assert not (bundle / "crash.dmp").exists()
    assert not list(bundle.rglob("__pycache__"))


def test_the_check_changes_nothing_and_points_at_prune(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)
    stderr = io.StringIO()

    with (
        mock.patch.object(check_bundle, "REPO", repo),
        mock.patch.object(check_bundle, "host_platform", return_value=PLATFORM),
        contextlib.redirect_stderr(stderr),
    ):
        assert check_bundle.main([str(bundle)]) == 0
        write(bundle / "crash.dmp", "dump")
        assert check_bundle.main([str(bundle)]) == 1
    assert (bundle / "crash.dmp").exists()
    assert stderr.getvalue().splitlines() == [
        "unexpected bundle entry crash.dmp (run with --prune)",
        f"1 bundle problems in {bundle}",
    ]


def test_the_scan_reports_files_that_name_the_build_machine(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bundle = make_bundle(tmp_path, repo)
    write(bundle / "tinytex" / "texmf-var" / "language.dat", "% made in C:\\Users\\builder\\Mathimator\n")
    write(bundle / "python" / "Lib" / "__pycache__" / "site.cpython-314.pyc", "C:\\Users\\builder\\Mathimator")

    pattern = re.compile(check_bundle._path_pattern(Path("C:/Users/builder/Mathimator")), re.IGNORECASE)
    assert check_bundle.scan(bundle, pattern) == ([], [bundle / "tinytex" / "texmf-var" / "language.dat"])


def test_read_pins_reads_key_value_lines(tmp_path: Path) -> None:
    write(tmp_path / "versions.env", PINS)

    pins = check_bundle.read_pins(tmp_path / "versions.env")
    assert pins["UV_VERSION"] == "0.13.5"
    assert pins["TLNET_REPOSITORY"] == "https://texlive.info/tlnet-archive/2026/10/23/tlnet"
    assert len(pins) == 6
