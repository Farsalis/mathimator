"""Prune packaging/bundle and fail when it would ship a trace of the build machine.

Everything in packaging/bundle ships in the installer as runtime/. This removes
top-level entries outside the bundle layout and every __pycache__ folder. It
then fails when a link leaves the bundle, or when a file names the checkout or
a path inside the builder's home folder, which names the account.

packaging/build-bundle.ps1 and build-bundle.sh run it before they write
runtime.json. Run it again right before `tauri build`. The installed app
compiles its own bytecode under the install path on the user's machine, but a
run of the bundled interpreter from this checkout, such as the offline tests,
writes bytecode into the bundle that names the checkout. When the bundled
interpreter runs this script, pass -B so that the check writes none itself.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# What the build scripts put in packaging/bundle. .gitkeep keeps the folder in
# git so that `tauri dev` compiles before the first bundle build.
LAYOUT = frozenset({"python", "tinytex", "typst", "worker", "samples", "runtime.json", ".gitkeep"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prune packaging/bundle and fail when it would ship a trace of the build machine."
    )
    parser.add_argument(
        "bundle",
        nargs="?",
        type=Path,
        default=REPO / "packaging" / "bundle",
        help="the bundle folder (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    bundle: Path = args.bundle
    if not bundle.is_dir():
        parser.error(f"{bundle} is not a folder")

    prune(bundle)
    outside, named = scan(bundle, build_paths())
    for link in outside:
        print(f"link leaves the bundle: {link}", file=sys.stderr)
    for path in named:
        print(f"build path in {path}", file=sys.stderr)
    if outside or named:
        print(f"{len(outside) + len(named)} bundle entries reach or name the build machine", file=sys.stderr)
        return 1
    return 0


def prune(bundle: Path) -> None:
    """Remove top-level entries outside LAYOUT, such as a crash dump, and all bytecode."""
    for entry in sorted(bundle.iterdir()):
        if entry.name not in LAYOUT:
            print(f"removing unexpected bundle entry {entry.name}")
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    for cache in sorted(bundle.rglob("__pycache__")):
        shutil.rmtree(cache)


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
    bundle is installed somewhere else.
    """
    resolved = bundle.resolve()
    outside: list[Path] = []
    named: list[Path] = []
    for top, dirnames, filenames in os.walk(bundle):
        here = Path(top)
        linked = [name for name in dirnames if _is_link(here / name)]
        dirnames[:] = [name for name in dirnames if name not in linked]
        for name in linked + filenames:
            path = here / name
            if _is_link(path):
                if Path(os.readlink(path)).is_absolute() or not path.resolve().is_relative_to(resolved):
                    outside.append(path)
            elif build_path.search(path.read_bytes()):
                named.append(path)
    return outside, named


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


if __name__ == "__main__":
    raise SystemExit(main())
