"""Start the supervisor for one project directory.

The scene file and the media directory live under that project. There is no
``--scene`` source of truth.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mathimator_worker.supervisor import Supervisor

FORBIDDEN_MODULES = {"manim", "av"}


def _configure_stdio() -> None:
    """JSON lines are UTF-8. A Windows pipe otherwise uses the ANSI code page."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(prog="mathimator-worker")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--python", required=True)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument(
        "--init",
        action="store_true",
        help="create an empty project.json when the folder does not have one",
    )
    args = parser.parse_args(argv)

    loaded = [name for name in sys.modules if name.split(".", 1)[0] in FORBIDDEN_MODULES]
    if loaded:
        sys.stderr.write(f"supervisor imported forbidden modules: {loaded}\n")
        return 2

    supervisor = Supervisor(
        project=args.project,
        python=args.python,
        log_path=args.log,
        init=args.init,
    )
    supervisor.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
