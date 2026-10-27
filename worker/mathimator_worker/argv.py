"""Manim command line and environment for the preview and export profiles.

Manim runs in the job's ``.mathimator/codegen`` folder, which holds nothing
but ``scene.py``, and takes that folder as its home, so no per-user or
per-folder manim.cfg applies. Its paths are relative to that folder, so
TeX never sees the user's folder names. Python runs with ``-P`` and
checks hash-based bytecode against its source, so nothing planted beside the
scene runs in its place. The interpreter is always a real one,
never a venv launcher.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from mathimator_worker.document import (
    DEFAULT_EXPORT_FPS,
    DEFAULT_EXPORT_HEIGHT,
    DEFAULT_EXPORT_WIDTH,
    FORMATS,
    PREVIEW_FPS,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    ProfileName,
)

SCENE_CLASS = "MainScene"
SEED = "1"
# Relative to the folder Manim runs in, .mathimator/codegen.
SCENE_FILE = "scene.py"
MEDIA_DIR = "../cache/media"
# Mathimator's Manim settings.
CONFIG_FILE = Path(__file__).with_name("manim.cfg")
# Runs Manim's command line with FFmpeg's quoting fixed.
LAUNCHER = "mathimator_worker.manim_main"

FORBIDDEN_TOKENS = (
    "--preview",
    "--live-preview",
    "--disable_caching",
    "--flush_cache",
    "--from_animation_number",
)

# kpathsea's settings for the LaTeX child.
_TEX_SETTINGS = {"shell_escape": "f", "openin_any": "p", "openout_any": "p"}


def manim_interpreter(python: str) -> tuple[str, str | None]:
    """The interpreter that runs Manim for ``--python``, and the venv launcher it stands in for.

    A venv's python.exe on Windows is a launcher that starts the real
    interpreter as its child, so Manim would run one process below the one put
    in its Job Object, and a TeX helper it starts could escape the job. When
    ``python`` is the launcher this worker runs under, as in a debug build,
    Manim runs the real interpreter instead, and ``__PYVENV_LAUNCHER__`` names
    the launcher so the venv still applies, as ``multiprocessing`` does
    (bpo-35797). Any other interpreter, such as the real one the installer
    ships, runs as it is.
    """
    base: str = getattr(sys, "_base_executable", sys.executable)
    if sys.platform != "win32" or base == sys.executable:
        return python, None
    try:
        launcher = os.path.samefile(python, sys.executable)
    except OSError:
        launcher = False
    return (base, sys.executable) if launcher else (python, None)


def profile_size(profile: ProfileName) -> tuple[int, int, int]:
    """The width, height and frame rate a render of ``profile`` has without a document, in hand-written mode."""
    if profile == "preview":
        return PREVIEW_WIDTH, PREVIEW_HEIGHT, PREVIEW_FPS
    return DEFAULT_EXPORT_WIDTH, DEFAULT_EXPORT_HEIGHT, DEFAULT_EXPORT_FPS


def build_manim_argv(
    python: str,
    profile: ProfileName,
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
    spec = FORMATS.get(fmt)
    if spec is None:
        raise ValueError(f"unknown format {fmt}")
    if transparent and spec.opaque_reason is not None:
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
        spec.cli_flag,
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
    """The environment of a Manim run in ``home``, the folder it runs in.

    ``home`` stands in for the user's home folder, so Manim reads no per-user
    manim.cfg. kpathsea reads ``shell_escape``, ``openin_any`` and
    ``openout_any`` from the environment before texmf.cnf, and reads
    ``shell_escape.latex`` and ``shell_escape_latex`` before
    ``shell_escape``, so every inherited variable that starts with one of
    those names goes. Whatever the user set, LaTeX runs no shell command and
    opens files as paranoidly as the TeX release allows. The formula
    allowlist keeps a formula from reading files, and the worker removes TeX
    inputs planted in Manim's TeX folder before each render. ``launcher`` is
    the venv launcher of ``manim_interpreter``, if any.

    ``PYTHONHASHSEED`` is fixed whatever the user set: Manim's key for a play
    serialises a function's global names in the order of Python's string
    hashing, which otherwise differs in every process, so a rotate or a wave
    play would miss Manim's cache on every render.
    """
    settings = {
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONHASHSEED": "0",
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
