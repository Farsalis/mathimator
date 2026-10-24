"""Check packaging/bundle before it ships, or prune it while it is built.

Everything in packaging/bundle ships in the installer as runtime/.

    check_bundle.py [BUNDLE]

changes nothing. Run it right before `tauri build`. It fails unless:

- runtime.json is a JSON object (contract C9.6) whose platform is this
  machine's, such as windows-x86_64. Its python, worker, sample, typst and
  tinytexBin are relative paths to files and folders inside the bundle, and
  python is a real interpreter rather than a venv launcher (D30, D33).
- lockSha256 is the SHA-256 of uv.lock (D34), and pythonVersion, typstVersion,
  tinytexRelease and tlnetRepository match packaging/versions.env (D35), so the
  bundle was built from this checkout's pins.
- The worker folder holds exactly worker/mathimator_worker, and the sample is
  tests/fixtures/reference-60/project.json, byte for byte.
- THIRD_PARTY_NOTICES.txt names the SHA-256 of this checkout's uv.lock,
  Cargo.lock and package-lock.json, so scripts/generate_notices.py wrote it for
  these dependencies (D75).
- The bundle holds nothing outside its layout, and its __pycache__ folders
  hold nothing but the bytecode that the build scripts compile: checked-hash
  bytecode of a source beside the folder, for the interpreter running this
  script.

    check_bundle.py --prune [BUNDLE]

is what packaging/build-bundle.ps1 and build-bundle.sh run before they write
runtime.json. It removes top-level entries outside the layout, such as a crash
dump or the venv of an older build (D64), and everything else in a __pycache__
folder, such as what Python writes on import, another Python version's
bytecode, or bytecode whose source is gone. It never enters a linked folder:
a link is removed itself, never what it points at. It skips the checks that
need runtime.json and the notices.

Both forms fail when a link leaves the bundle, or when a file, bytecode
included, names the checkout or a path inside the builder's home folder, which
names the account. The Windows installer puts the runtime under Program Files
(D62), where Python run by a standard user cannot save the bytecode it
compiles, so the build scripts compile the bundled Python and the worker
beforehand and record each source as runtime/... rather than by its build
path. Checked-hash bytecode holds a hash of its source, which Python checks on
import, so a stale file never runs in place of its source. Run this script
with the bundled interpreter, whose bytecode it keeps, and pass -I -B so that
the check reads no PYTHON* variable and writes no bytecode itself.

BUNDLE defaults to packaging/bundle. The exit status is 0 when the bundle
passes and 1, with one line per problem on stderr, when it does not.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath

REPO = Path(__file__).resolve().parents[1]
NOTICES = "THIRD_PARTY_NOTICES.txt"
# What the build scripts and scripts/generate_notices.py put in
# packaging/bundle. .gitkeep keeps the folder in git so that `tauri dev`
# compiles before the first bundle build.
LAYOUT = frozenset({"python", "tinytex", "typst", "worker", "samples", "runtime.json", NOTICES, ".gitkeep"})
# The runtime.json paths the installer needs, and whether each names a folder.
# The shell treats typst and tinytexBin as optional (C9.6), but the bundle
# scripts always ship both, so a bundle without them is broken.
PATHS = {"python": False, "worker": True, "sample": False, "typst": False, "tinytexBin": True}
# Every runtime.json field the check reads. The bundle scripts write them all;
# the ones after the paths tie the bundle to uv.lock and packaging/versions.env.
FIELDS = ("platform", *PATHS, "lockSha256", "pythonVersion", "typstVersion", "tinytexRelease", "tlnetRepository")
# The lockfiles whose packages the notices list.
LOCKS = ("uv.lock", "desktop/src-tauri/Cargo.lock", "desktop/package-lock.json")
# Bytes 4-7 of a checked-hash .pyc, its PEP 552 flags: hash-based, and checked
# against its source on import.
CHECKED_HASH = (0b11).to_bytes(4, "little")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check packaging/bundle before `tauri build`, or prune it while it is built."
    )
    parser.add_argument(
        "bundle",
        nargs="?",
        type=Path,
        default=REPO / "packaging" / "bundle",
        help="the bundle folder (default: %(default)s)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="remove entries outside the layout and everything in __pycache__ but the bytecode the build"
        " compiles, and skip the runtime.json and notices checks",
    )
    args = parser.parse_args(argv)
    bundle: Path = args.bundle
    if not bundle.is_dir():
        parser.error(f"{bundle} is not a folder")

    if args.prune:
        prune(bundle)
        problems = []
    else:
        problems = verify(bundle, REPO, host_platform())
    outside, named = scan(bundle, build_paths())
    problems += [f"link leaves the bundle: {link}" for link in outside]
    problems += [f"build path in {path}" for path in named]
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} bundle problems in {bundle}", file=sys.stderr)
        return 1
    return 0


def host_platform() -> str:
    """The runtime.json platform of this machine, such as windows-x86_64."""
    system = "windows" if sys.platform == "win32" else sys.platform
    machine = platform.machine().lower()
    return f"{system}-{'x86_64' if machine == 'amd64' else machine}"


def prune(bundle: Path) -> None:
    """Remove top-level entries outside LAYOUT, such as a crash dump, and bytecode the build did not compile."""
    for entry in _unexpected(bundle):
        print(f"removing unexpected bundle entry {entry.name}")
        _remove(entry)
    stray = _stray_bytecode(bundle)
    if stray:
        print(f"removing {len(stray)} files from __pycache__ folders that the build did not compile")
    for path in stray:
        _remove(path)


def verify(bundle: Path, repo: Path, expected_platform: str) -> list[str]:
    """Return what keeps the bundle from shipping, built from repo, on expected_platform."""
    problems = [f"unexpected bundle entry {entry.name} (run with --prune)" for entry in _unexpected(bundle)]
    stray = _stray_bytecode(bundle)
    if stray:
        problems.append(f"{len(stray)} files in __pycache__ folders were not compiled by the build (run with --prune)")
    try:
        runtime = json.loads((bundle / "runtime.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append("runtime.json is missing: build the bundle with packaging/build-bundle.ps1 or .sh")
    except (OSError, ValueError) as error:
        problems.append(f"runtime.json is unreadable: {error}")
    else:
        if isinstance(runtime, dict):
            problems += _runtime_problems(runtime, bundle, repo, expected_platform)
        else:
            problems.append("runtime.json is not a JSON object")
    return problems + _notices_problems(bundle, repo)


def _runtime_problems(runtime: dict[str, object], bundle: Path, repo: Path, expected_platform: str) -> list[str]:
    fields: dict[str, str] = {}
    problems: list[str] = []
    for name in FIELDS:
        value = runtime.get(name)
        if isinstance(value, str) and value:
            fields[name] = value
        else:
            problems.append(f"runtime.json has no {name}")

    if "platform" in fields and fields["platform"] != expected_platform:
        problems.append(f"runtime.json is for {fields['platform']}, not {expected_platform}")
    paths: dict[str, Path] = {}
    for name, is_folder in PATHS.items():
        if name not in fields:
            continue
        path = _inside(bundle, fields[name])
        if path is None:
            problems.append(f"runtime.json {name} {fields[name]!r} is not a relative path inside the bundle")
        elif not (path.is_dir() if is_folder else path.is_file()):
            problems.append(f"runtime.json {name} {fields[name]} is not in the bundle")
        else:
            paths[name] = path
    if "python" in paths and any((folder / "pyvenv.cfg").exists() for folder in paths["python"].parents[:2]):
        problems.append(f"{fields['python']} is a venv launcher, not the real interpreter (D33)")
    if "worker" in paths:
        sources = {f"mathimator_worker/{name}": data for name, data in _files(repo / "worker" / "mathimator_worker")}
        stale = _differences(sources, dict(_files(paths["worker"])))
        problems += [f"bundled worker is stale: {difference}" for difference in stale]
    if "sample" in paths:
        fixture = repo / "tests" / "fixtures" / "reference-60" / "project.json"
        if paths["sample"].read_bytes() != fixture.read_bytes():
            problems.append(f"bundled sample {fields['sample']} differs from tests/fixtures/reference-60/project.json")

    if "lockSha256" in fields and fields["lockSha256"] != _sha256(repo / "uv.lock"):
        problems.append("runtime.json lockSha256 is not the SHA-256 of uv.lock: rebuild the bundle")
    pins = read_pins(repo / "packaging" / "versions.env")
    pinned = {
        "pythonVersion": (pins["PYTHON_VERSION"], pins["PYTHON_FALLBACK_VERSION"]),
        "typstVersion": (f"typst {pins['TYPST_VERSION']}",),
        "tinytexRelease": (pins["TINYTEX_VERSION"],),
        "tlnetRepository": (pins["TLNET_REPOSITORY"],),
    }
    for name, allowed in pinned.items():
        if name not in fields:
            continue
        # typstVersion is the `typst --version` line, such as "typst 0.15.0 (3ae52774)".
        built = " ".join(fields[name].split()[:2]) if name == "typstVersion" else fields[name]
        if built not in allowed:
            problems.append(
                f"runtime.json {name} is {fields[name]!r}, but packaging/versions.env pins {' or '.join(allowed)}"
            )
    return problems


def _notices_problems(bundle: Path, repo: Path) -> list[str]:
    try:
        notices = (bundle / NOTICES).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{NOTICES} is missing: run scripts/generate_notices.py"]
    stale = [lock for lock in LOCKS if f"{lock} SHA-256 {_sha256(repo / lock)}" not in notices]
    return [f"{NOTICES} predates {lock}: run scripts/generate_notices.py" for lock in stale]


def read_pins(path: Path) -> dict[str, str]:
    """Read packaging/versions.env: KEY=VALUE lines, comments and blanks skipped."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := re.fullmatch(r"([A-Z0-9_]+)=(.*)", line):
            pins[match[1]] = match[2]
    return pins


def _inside(bundle: Path, value: str) -> Path | None:
    """bundle/value, or None when value is absolute or climbs out of the bundle."""
    relative = PurePosixPath(value.replace("\\", "/"))
    if relative.is_absolute() or PureWindowsPath(value).anchor or ".." in relative.parts:
        return None
    return bundle / relative


def _unexpected(bundle: Path) -> list[Path]:
    return [entry for entry in sorted(bundle.iterdir()) if entry.name not in LAYOUT]


def _remove(entry: Path) -> None:
    """Delete entry; a link is removed itself, never what it points at."""
    if entry.is_dir() and not _is_link(entry):
        shutil.rmtree(entry)
    else:
        entry.unlink()


def _stray_bytecode(bundle: Path) -> list[Path]:
    """Everything in a __pycache__ folder but the bytecode the build scripts compile."""
    return [
        path
        for folder, _, _ in _walk(bundle)
        if folder.name == "__pycache__"
        for path in sorted(folder.iterdir())
        if not _is_compiled(path)
    ]


def _is_compiled(path: Path) -> bool:
    """Whether path is checked-hash bytecode that this interpreter compiles for a source beside its folder."""
    try:
        source = Path(importlib.util.source_from_cache(path))
    except ValueError:
        return False
    if Path(importlib.util.cache_from_source(source)) != path or not (source.is_file() and path.is_file()):
        return False
    with path.open("rb") as file:
        return file.read(8)[4:] == CHECKED_HASH


def _files(root: Path) -> list[tuple[str, bytes]]:
    """Every file under root but bytecode, by its relative POSIX path."""
    return [
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    ]


def _differences(expected: dict[str, bytes], actual: dict[str, bytes]) -> list[str]:
    missing = [f"{name} is missing" for name in expected if name not in actual]
    extra = [f"{name} is not in the sources" for name in actual if name not in expected]
    changed = [f"{name} differs" for name in expected if name in actual and actual[name] != expected[name]]
    return sorted(missing + extra + changed)


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def build_paths() -> re.Pattern[bytes]:
    """Match the checkout path, and any path inside the builder's home folder.

    The home folder must be followed by a separator: TeX Live's own scripts
    contain a bare /root, which is HOME in a root container.
    """
    script = Path(os.path.abspath(__file__))
    patterns = [_path_pattern(root) for root in {script.parents[1], script.resolve().parents[1]}]
    home = Path.home()
    if home.parent != home:
        patterns.append(_path_pattern(home) + rb"[\\/]")
    return re.compile(b"|".join(patterns), re.IGNORECASE if os.name == "nt" else 0)


def _path_pattern(path: Path) -> bytes:
    """Match path written with \\, / or JSON's \\\\ between its parts."""
    parts = re.split(r"[\\/]+", str(path).rstrip("\\/"))
    return rb"[\\/]+".join(re.escape(part.encode()) for part in parts)


def scan(bundle: Path, build_path: re.Pattern[bytes]) -> tuple[list[Path], list[Path]]:
    """Return the links that leave the bundle and the files that match build_path.

    Links are not followed. A link must be relative and stay inside the
    bundle: an absolute target names the build machine, and it breaks once the
    bundle is installed somewhere else. Bytecode is read like any other file:
    the build scripts record each source in it as runtime/..., so a build path
    there is a leak.
    """
    resolved = bundle.resolve()
    outside: list[Path] = []
    named: list[Path] = []
    for here, linked, filenames in _walk(bundle):
        for name in linked + filenames:
            path = here / name
            if _is_link(path):
                if Path(os.readlink(path)).is_absolute() or not path.resolve().is_relative_to(resolved):
                    outside.append(path)
            elif build_path.search(path.read_bytes()):
                named.append(path)
    return outside, named


def _walk(bundle: Path) -> Iterator[tuple[Path, list[str], list[str]]]:
    """Yield each folder of bundle with its linked folders and its files, never entering a link."""
    for top, dirnames, filenames in os.walk(bundle):
        here = Path(top)
        linked = [name for name in dirnames if _is_link(here / name)]
        dirnames[:] = [name for name in dirnames if name not in linked]
        yield here, linked, filenames


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


if __name__ == "__main__":
    raise SystemExit(main())
