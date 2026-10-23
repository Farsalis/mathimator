"""Manim command line for the preview and export profiles."""

from __future__ import annotations

from pathlib import Path

SCENE_CLASS = "MainScene"
SEED = "1"

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

_FORMAT_FLAGS = {
    "mp4": "mp4",
    "gif": "gif",
    "webm": "webm",
    "png_sequence": "png-sequence",
}


def profile_size(profile: str) -> tuple[int, int, int]:
    if profile == "preview":
        return PREVIEW_WIDTH, PREVIEW_HEIGHT, PREVIEW_FPS
    if profile == "export":
        return EXPORT_WIDTH, EXPORT_HEIGHT, EXPORT_FPS
    raise ValueError(f"unknown profile {profile}")


def build_manim_argv(
    python: str,
    scene: Path,
    media_dir: Path,
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
    project seed, and one encoder in flight stay on every command.
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
        "-m",
        "manim",
        "render",
        scene.as_posix(),
        SCENE_CLASS,
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
            media_dir.as_posix(),
            "--max-inflight-encoders",
            "1",
            "--progress_bar",
            "none",
        ]
    )
    _reject_forbidden(argv)
    return argv


def _reject_forbidden(argv: list[str]) -> None:
    for token in argv:
        if token in FORBIDDEN_TOKENS or token == "opengl":
            raise ValueError(f"refusing Manim argument {token}")
    renderer_at = argv.index("--renderer")
    if argv[renderer_at + 1] != "cairo":
        raise ValueError("renderer must be cairo")
