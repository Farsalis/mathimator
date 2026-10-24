"""Fail when the app version or a toolchain pin disagrees with its other copies (D72).

    check_versions.py [--tag TAG]

The app version is written in worker/pyproject.toml, mathimator_worker's
__version__, desktop/package.json, desktop/src-tauri/Cargo.toml and
tauri.conf.json, in the lockfiles uv.lock, package-lock.json and Cargo.lock,
and in .bumpversion.toml, whose `bump-my-version bump` rewrites all of them.
The root pyproject.toml is not the app: uv needs a version there, so it holds
0.0.0. tauri.conf.json may leave its version out, and Tauri then reads
Cargo.toml's.

Each toolchain pin must match the source it copies:

- the Rust channel in rust-toolchain.toml, the release that Cargo.toml's
  rust-version names (1.93.0 for 1.93 or 1.93.0);
- Node in desktop/.nvmrc, the one major that package.json engines.node
  allows (">=24 <25" for 24);
- .python-version, PYTHON_VERSION in packaging/versions.env, because the dev
  interpreter is the bundle's (D39);
- [tool.uv] required-version, UV_VERSION in versions.env;
- MANIM_VERSION in mathimator_worker/document.py, the Manim pin in the root
  pyproject.toml's render group and constraint-dependencies;
- TYPST_VERSION in versions.env, the version that the guard in the shell's
  equation.rs requires of `typst --version`, its starts_with("typst X.Y.Z").

With --tag, as the release pipeline passes $CI_COMMIT_TAG, the tag must be v
followed by the app version.

The script needs only the standard library. It exits 0 when everything
agrees, and 1 with one line per disagreement on stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when the app version or a toolchain pin disagrees with its other copies."
    )
    parser.add_argument("--tag", help="a release tag, which must be v followed by the app version")
    args = parser.parse_args(argv)
    try:
        versions = app_versions(REPO)
        problems = version_problems(versions, args.tag) + pin_problems(REPO)
    except (OSError, ValueError, LookupError) as error:
        print(f"cannot read a version: {error!r}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"app version {versions['desktop/src-tauri/Cargo.toml']}; toolchain pins agree")
    return 0


def app_versions(repo: Path) -> dict[str, str]:
    """The app version as each file that records it has it, by file."""
    desktop = repo / "desktop"
    tauri = desktop / "src-tauri"
    package_lock = _json(desktop / "package-lock.json")
    versions = {
        ".bumpversion.toml": _toml(repo / ".bumpversion.toml")["tool"]["bumpversion"]["current_version"],
        "worker/pyproject.toml": _toml(repo / "worker" / "pyproject.toml")["project"]["version"],
        "worker/mathimator_worker/__init__.py": _search(
            r'^__version__ = "(.+)"$', repo / "worker" / "mathimator_worker" / "__init__.py"
        ),
        "uv.lock": _locked(_toml(repo / "uv.lock"), "mathimator-worker"),
        "desktop/package.json": _json(desktop / "package.json")["version"],
        "desktop/package-lock.json": package_lock["version"],
        'desktop/package-lock.json packages[""]': package_lock["packages"][""]["version"],
        "desktop/src-tauri/Cargo.toml": _toml(tauri / "Cargo.toml")["package"]["version"],
        "desktop/src-tauri/Cargo.lock": _locked(_toml(tauri / "Cargo.lock"), "mathimator"),
    }
    conf = _json(tauri / "tauri.conf.json")
    if "version" in conf:
        versions["desktop/src-tauri/tauri.conf.json"] = conf["version"]
    return {name: str(version) for name, version in versions.items()}


def version_problems(versions: dict[str, str], tag: str | None) -> list[str]:
    """Report app versions that disagree, and a tag that is not v<version>."""
    if len(set(versions.values())) > 1:
        return [
            "the app version differs between files:",
            *(f"  {name}: {version}" for name, version in versions.items()),
        ]
    version = next(iter(versions.values()))
    if tag is not None and tag != f"v{version}":
        return [f"tag {tag} does not match the app version {version}: expected v{version}"]
    return []


def pin_problems(repo: Path) -> list[str]:
    """Report every toolchain pin that does not match its source."""
    pins = read_pins(repo / "packaging" / "versions.env")
    root = _toml(repo / "pyproject.toml")
    desktop = repo / "desktop"
    tauri = desktop / "src-tauri"
    problems: list[str] = []

    channel = _toml(tauri / "rust-toolchain.toml")["toolchain"]["channel"]
    rust_version = _toml(tauri / "Cargo.toml")["package"]["rust-version"]
    if not f"{channel}.".startswith(f"{rust_version}."):
        problems.append(f"rust-toolchain.toml pins Rust {channel}, but Cargo.toml rust-version is {rust_version}")

    node = (desktop / ".nvmrc").read_text(encoding="utf-8").strip()
    major = int(node.removeprefix("v").split(".")[0])
    engines = _json(desktop / "package.json")["engines"]["node"]
    if engines != f">={major} <{major + 1}":
        problems.append(f"desktop/.nvmrc pins Node {node}, but package.json engines.node is {engines!r}")

    python = (repo / ".python-version").read_text(encoding="utf-8").strip()
    if python != pins["PYTHON_VERSION"]:
        problems.append(f".python-version is {python}, but packaging/versions.env pins {pins['PYTHON_VERSION']}")

    required_uv = root["tool"]["uv"]["required-version"]
    if required_uv != f"=={pins['UV_VERSION']}":
        problems.append(
            f"pyproject.toml requires uv {required_uv}, but packaging/versions.env pins {pins['UV_VERSION']}"
        )

    manim = _search(r'^MANIM_VERSION = "(.+)"$', repo / "worker" / "mathimator_worker" / "document.py")
    if f"manim[typst]=={manim}" not in root["dependency-groups"]["render"]:
        problems.append(f"the pyproject.toml render group does not pin manim[typst]=={manim} (document.py)")
    if f"manim=={manim}" not in root["tool"]["uv"]["constraint-dependencies"]:
        problems.append(f"the pyproject.toml constraint-dependencies do not pin manim=={manim} (document.py)")

    equation = (tauri / "src" / "equation.rs").read_text(encoding="utf-8")
    required_typst = sorted(set(re.findall(r'starts_with\("typst (\d+\.\d+\.\d+)"\)', equation)))
    if not required_typst:
        problems.append('equation.rs has no starts_with("typst X.Y.Z") guard on the Typst version')
    elif required_typst != [pins["TYPST_VERSION"]]:
        problems.append(
            f"equation.rs requires Typst {', '.join(required_typst)}, "
            f"but packaging/versions.env pins {pins['TYPST_VERSION']}"
        )
    return problems


def read_pins(path: Path) -> dict[str, str]:
    """Read packaging/versions.env: KEY=VALUE lines, comments and blanks skipped."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := re.fullmatch(r"([A-Z0-9_]+)=(.*)", line):
            pins[match[1]] = match[2]
    return pins


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def _json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _locked(lock: dict[str, Any], name: str) -> str:
    """The version of package name in a uv.lock or Cargo.lock."""
    version = next((package["version"] for package in lock["package"] if package["name"] == name), None)
    if version is None:
        raise LookupError(f"the lockfile has no package {name}")
    return str(version)


def _search(pattern: str, path: Path) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ValueError(f"{path} does not match {pattern}")
    return match[1]


if __name__ == "__main__":
    raise SystemExit(main())
