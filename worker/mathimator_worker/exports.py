"""Finding, checking and publishing a finished export (spec section 6, C7.2).

Manim writes an export into the project's media folder. The supervisor finds
it there, checks that it is this export and not the preview, and copies it
into ``.mathimator/exports/<key16>/`` with an ``export.json`` that records its
key. A published export whose ``export.json`` records the current key is
returned without rendering again, unless the user asks for a re-export (D56).
"""

from __future__ import annotations

import re
from pathlib import Path

from mathimator_worker.argv import SCENE_CLASS
from mathimator_worker.document import FORMATS, MANIM_VERSION, PREVIEW_FPS, PREVIEW_HEIGHT, Project, total_duration
from mathimator_worker.identity import export_key, scene_hash
from mathimator_worker.jsonio import dumps_record, read_record
from mathimator_worker.layout import ProjectLayout
from mathimator_worker.project_folder import copy_atomically, make_folders, os_reason, write_atomically

CLOSE_AND_RETRY = "If the file is open in another program, close it and export again."
# Manim's closing line for a PNG sequence. Rich may wrap the folder onto later lines, but the count stays with "images".
_IMAGES_READY = re.compile(r"(\d+)\s+images\s+ready\s+at")
# The quality folder of the preview, whose movie is never published.
_PREVIEW_QUALITY = f"{PREVIEW_HEIGHT}p{PREVIEW_FPS}"


class PublishFailed(Exception):
    """A finished export that could not be published. The message is for the user (C5.2)."""


def find_export_output(layout: ProjectLayout, quality: str, fmt: str) -> Path | None:
    """Locate the primary artifact Manim wrote for one export format.

    GIF files are named ``MainScene_ManimCE_v<version>.gif``. MP4 and WebM use
    ``MainScene.<ext>``. PNG sequence is the ``images/<stem>/MainScene`` directory.
    A preview MP4 in ``480p15`` is not returned for another format.
    """
    if fmt == "png_sequence":
        images = layout.png_frames_dir
        if images.is_dir() and any(images.glob("*.png")):
            return images
        return None
    spec = FORMATS.get(fmt)
    if spec is None:
        return None
    folder = layout.video_dir(quality)
    if not folder.is_dir():
        return None
    exact = folder / f"{SCENE_CLASS}{spec.suffix}"
    if exact.is_file():
        return exact
    matches = [path for path in folder.glob(f"{SCENE_CLASS}*{spec.suffix}") if path.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    return matches[0]


def export_frame_count(project: Project) -> int:
    """The frames of an export of ``project``: its total, to the nearest frame at the export rate (D63).

    The total is whole milliseconds and a half frame rounds up, as for every
    beat boundary, so this is integer math: in floats, 4.1 s at 15 fps is
    61.4999... frames and would round down.
    """
    return (round(total_duration(project) * 1000) * project.export_profile.fps + 500) // 1000


def frame_count_problem(frames: Path, output: str, expected: int) -> str | None:
    """Describe why a PNG sequence cannot be published, or return None (F021, spec section 7).

    The frames in the folder must be as many as Manim reports in ``output``,
    the last lines it wrote, which guards against frames left from before.
    They must also be the ``expected`` frames of the document, one PNG each.
    """
    written = sum(1 for _ in frames.glob("*.png"))
    reported = _IMAGES_READY.findall(output)
    if not reported:
        return f"manim did not report how many PNG frames it wrote to {frames}"
    if written != int(reported[-1]):
        return f"manim reported {reported[-1]} PNG frames, but {frames} holds {written}"
    if written != expected:
        return f"the scene has {expected} frames, but manim wrote {written} PNG frames to {frames}"
    return None


def existing_export(layout: ProjectLayout, project: Project) -> Path | None:
    """The published export of ``project``, when its export.json records the current key, or None."""
    folder = layout.export_dir(project)
    manifest_path = folder / "export.json"
    artifact = folder / FORMATS[project.export_profile.format].artifact_name
    if project.export_profile.format == "png_sequence":
        ready = artifact.is_dir() and any(artifact.glob("*.png"))
    else:
        ready = artifact.is_file()
    if not ready or not manifest_path.is_file():
        return None
    manifest = read_record(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("exportKey") != export_key(project):
        return None
    return artifact


def publish_export(layout: ProjectLayout, captured: Project, movie: Path, quality: str) -> Path:
    """Copy the finished export ``movie`` and its export.json into ``exports/<key16>/``. Return the published path.

    ``captured`` is the document the export rendered, in the ``quality``
    folder of the project at ``layout``. Only ``exports/`` is written, so this
    needs no document lock (F031, F164). The copy replaces the older file of
    the same key only once it is whole, and export.json is written after it,
    so a failure leaves both as they were (C7.2). Raises PublishFailed with
    the message.
    """
    folder = layout.export_dir(captured)
    fmt = captured.export_profile.format
    target = folder / FORMATS[fmt].artifact_name
    problem = _export_problem(movie, target, fmt, layout.media_dir, quality)
    if problem is not None:
        raise PublishFailed(f"Could not save the export to {target}: {problem}.")
    try:
        make_folders(layout.root, folder)
        copy_atomically(movie, target)
        write_atomically(folder / "export.json", dumps_record(_export_manifest(captured)).encode("utf-8"))
    except OSError as exc:
        reason = f"Could not save the export to {target}: {os_reason(exc)}. {CLOSE_AND_RETRY}"
        raise PublishFailed(reason) from exc
    return target


def _export_problem(movie: Path, target: Path, fmt: str, media_dir: Path, quality: str) -> str | None:
    """Why ``movie`` must not be published as a ``fmt`` export at ``target``, or None (the preview-file guard).

    The path is judged below ``media_dir`` only, so a user folder named
    480p15 above the project does not look like the preview (G4.9).
    """
    try:
        parts = movie.resolve().relative_to(media_dir.resolve()).parts
    except ValueError:
        return "the render is not in the project's media folder"
    if fmt == "png_sequence":
        if not movie.is_dir() or not any(movie.glob("*.png")):
            return "the render holds no PNG frames"
        # A preview movie lives under 480p15. PNG frames do not, unless a caller hands that folder over by mistake.
        return "the render is the preview" if _PREVIEW_QUALITY in parts else None
    if quality not in parts or (_PREVIEW_QUALITY in parts and quality != _PREVIEW_QUALITY):
        return "the render is the preview"
    if not movie.is_file() or movie.suffix.lower() != FORMATS[fmt].suffix:
        return f"the render is not a {fmt.upper()} file"
    if movie.resolve() == target.resolve():
        return "the render is the published file itself"
    return None


def _export_manifest(captured: Project) -> dict[str, object]:
    """The export.json beside a published export, which makes it the export of its key."""
    profile = captured.export_profile
    return {
        "exportKey": export_key(captured),
        "manimVersion": MANIM_VERSION,
        "generatorVersion": captured.generator_version,
        "sceneHash": scene_hash(captured),
        "file": FORMATS[profile.format].artifact_name,
        "profile": {
            "format": profile.format,
            "width": profile.width,
            "height": profile.height,
            "fps": profile.fps,
            "transparent": profile.transparent,
        },
    }
