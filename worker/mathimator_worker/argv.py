"""Manim command line and environment for the preview and export profiles.

Manim runs in the job's ``.mathimator/codegen`` folder, which holds nothing
but ``scene.py``, and takes that folder as its home, so no per-user or
per-folder manim.cfg applies (D60). Its paths are relative to that folder, so
TeX never sees the user's folder names (D61). Python runs with ``-P`` and
checks hash-based bytecode against its source, so nothing planted beside the
scene runs in its place (F124, F170). The interpreter is always a real one,
never a venv launcher (D16).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

SCENE_CLASS = "MainScene"
SEED = "1"
# Relative to the folder Manim runs in, .mathimator/codegen.
SCENE_FILE = "scene.py"
MEDIA_DIR = "../cache/media"
# Mathimator's Manim settings (D60).
CONFIG_FILE = Path(__file__).with_name("manim.cfg")
# Runs Manim's command line with FFmpeg's quoting fixed (D61).
LAUNCHER = "mathimator_worker.manim_main"

PREVIEW_WIDTH = 854
PREVIEW_HEIGHT = 480
PREVIEW_FPS = 15

EXPORT_WIDTH = 1920
EXPORT_HEIGHT = 1080
EXPORT_FPS = 30

FORBIDDEN_TOKENS = (
    "--preview",
    "--live-preview",
    "--disable_caching",
    "--flush_cache",
    "--from_animation_number",
)

# kpathsea's settings for the LaTeX child (D18).
_TEX_SETTINGS = {"shell_escape": "f", "openin_any": "p", "openout_any": "p"}

_FORMAT_FLAGS = {
    "mp4": "mp4",
    "gif": "gif",
    "webm": "webm",
    "png_sequence": "png-sequence",
}


def manim_interpreter(python: str) -> tuple[str, str | None]:
    """The interpreter that runs Manim for ``--python``, and the venv launcher it stands in for (D16).

    A venv's python.exe on Windows is a launcher that starts the real
    interpreter as its child, so Manim would run one process below the one put
    in its Job Object, and a TeX helper it starts could escape the job. When
    ``python`` is the launcher this worker runs under, as in a debug build,
    Manim runs the real interpreter instead, and ``__PYVENV_LAUNCHER__`` names
    the launcher so the venv still applies, as ``multiprocessing`` does
    (bpo-35797). Any other interpreter, such as the real one the installer
    ships (D33), runs as it is.
    """
    base: str = getattr(sys, "_base_executable", sys.executable)
    if sys.platform != "win32" or base == sys.executable:
        return python, None
    try:
        launcher = os.path.samefile(python, sys.executable)
    except OSError:
        launcher = False
    return (base, sys.executable) if launcher else (python, None)


def profile_size(profile: str) -> tuple[int, int, int]:
    if profile == "preview":
        return PREVIEW_WIDTH, PREVIEW_HEIGHT, PREVIEW_FPS
    if profile == "export":
        return EXPORT_WIDTH, EXPORT_HEIGHT, EXPORT_FPS
    raise ValueError(f"unknown profile {profile}")


def build_manim_argv(
    python: str,
    profile: str,
    seed: str = SEED,
    *,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    fmt: str = "mp4",
    transparent: bool = False,
) -> list[str]:
    """Return the section 5 command line for one profile.

    Preview is 854×480 at 15 fps and stays an opaque mp4. Export uses the
    document's format. ``png_sequence`` is ``--format png-sequence``.
    ``--transparent`` is added only when that format allows it. Cairo, the
    project seed, one encoder in flight, Mathimator's manim.cfg and
    ``--silent``, which keeps Manim from asking PyPI for a newer release, stay
    on every command.
    """
    default_width, default_height, default_fps = profile_size(profile)
    if width is None:
        width = default_width
    if height is None:
        height = default_height
    if fps is None:
        fps = default_fps
    if fmt not in _FORMAT_FLAGS:
        raise ValueError(f"unknown format {fmt}")
    if transparent and fmt in {"mp4", "gif"}:
        raise ValueError(f"{fmt} cannot be transparent")
    if profile == "preview" and (fmt != "mp4" or transparent):
        raise ValueError("preview stays an opaque mp4")
    argv = [
        python,
        "-P",
        "--check-hash-based-pycs",
        "always",
        "-m",
        LAUNCHER,
        "render",
        SCENE_FILE,
        SCENE_CLASS,
        "--config_file",
        str(CONFIG_FILE),
        "--silent",
        "--renderer",
        "cairo",
        "--seed",
        seed,
        "-r",
        f"{width},{height}",
        "--fps",
        str(fps),
        "--format",
        _FORMAT_FLAGS[fmt],
    ]
    if transparent:
        argv.append("--transparent")
    argv.extend(
        [
            "--media_dir",
            MEDIA_DIR,
            "--max-inflight-encoders",
            "1",
            "--progress_bar",
            "none",
        ]
    )
    _reject_forbidden(argv)
    return argv


def manim_environment(base: Mapping[str, str], home: Path, launcher: str | None = None) -> dict[str, str]:
    """The environment of a Manim run in ``home``, the folder it runs in (D60, D18).

    ``home`` stands in for the user's home folder, so Manim reads no per-user
    manim.cfg. kpathsea reads ``shell_escape``, ``openin_any`` and
    ``openout_any`` from the environment before texmf.cnf, and reads
    ``shell_escape.latex`` and ``shell_escape_latex`` before
    ``shell_escape``, so every inherited variable that starts with one of
    those names goes. Whatever the user set, LaTeX runs no shell command and
    opens files as paranoidly as the TeX release allows. The formula
    allowlist keeps a formula from reading files, and the worker removes TeX
    inputs planted in Manim's TeX folder before each render. ``launcher`` is
    the venv launcher of ``manim_interpreter``, if any (D16).
    """
    settings = {
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "COLUMNS": "240",
        "NO_COLOR": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
        **_TEX_SETTINGS,
    }
    if launcher is not None:
        settings["__PYVENV_LAUNCHER__"] = launcher
    # Windows matches variable names in any case, so a user's SHELL_ESCAPE must not stay beside shell_escape.
    names = {name.upper() for name in settings}
    tex_names = tuple(name.upper() for name in _TEX_SETTINGS)
    environment = {
        name: value
        for name, value in base.items()
        if name.upper() not in names and not name.upper().startswith(tex_names)
    }
    environment.update(settings)
    return environment


def _reject_forbidden(argv: list[str]) -> None:
    for token in argv:
        if token in FORBIDDEN_TOKENS or token == "opengl":
            raise ValueError(f"refusing Manim argument {token}")
    renderer_at = argv.index("--renderer")
    if argv[renderer_at + 1] != "cairo":
        raise ValueError("renderer must be cairo")
