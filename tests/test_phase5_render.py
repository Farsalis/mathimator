"""Export matrix for phase 5. Linux wall times are not section 2 budget results."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from tests.support import REPO, SupervisorClient, media_dir, probe_video, sha256_file, start_supervisor

pytestmark = pytest.mark.render

PHASE5 = REPO / "out" / "phase5"
FIXTURES = REPO / "tests" / "fixtures"
FORBIDDEN = ("--preview", "--live-preview", "--disable_caching", "opengl")


def test_lesson_60_gif_defaults() -> None:
    project = PHASE5 / "lesson-60"
    log_path = PHASE5 / "logs" / "lesson-60.log"
    _reset(project)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    client = start_supervisor(project, log_path)
    try:
        _open_fixture(client, FIXTURES / "lesson-60" / "project.json", project)
        client.send({"cmd": "edit", "id": "gif", "edit": _profile("gif", 854, 480, 15, False)})
        assert _wait_state(client, "gif")["event"] == "state"
        started = time.perf_counter()
        client.send({"cmd": "export", "id": "gif-export"})
        done = client.wait_for_end("gif-export", 900)
        seconds = time.perf_counter() - started
    finally:
        client.close()
    if done.get("event") != "done":
        raise AssertionError(json.dumps(done, indent=2))
    path = Path(str(done["path"]))
    duration, width, height = probe_video(path)
    argv = _argv_with(log_path, "gif")
    _assert_flags(argv, "gif", "854,480", "15", transparent=False)
    assert (width, height) == (854, 480)
    assert abs(duration - 60.0) <= (1.0 / 15.0)
    assert "exports" in path.parts
    assert path.suffix == ".gif"
    assert "480p15" not in path.parts
    _write(
        PHASE5 / "lesson-60-gif.json",
        {
            "format": "gif",
            "path": str(path),
            "width": width,
            "height": height,
            "duration": duration,
            "seconds": seconds,
            "budget": "not a budget result",
            "argv": argv,
        },
    )


def test_reference_60_mp4_and_second_export_skips_manim() -> None:
    project = PHASE5 / "reference-60"
    log_path = PHASE5 / "logs" / "reference-60.log"
    _reset(project)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    client = start_supervisor(project, log_path)
    try:
        _open_fixture(client, FIXTURES / "reference-60" / "project.json", project)
        videos = media_dir(project) / "videos"
        if videos.exists():
            shutil.rmtree(videos)
        client.send({"cmd": "render_preview", "id": "preview"})
        preview_done = client.wait_for_end("preview", 900)
        if preview_done.get("event") != "done":
            raise AssertionError(json.dumps(preview_done, indent=2))
        preview_path = Path(str(preview_done["path"]))
        before = _argv_count(log_path)
        started = time.perf_counter()
        client.send({"cmd": "export", "id": "mp4"})
        exported = client.wait_for_end("mp4", 900)
        seconds = time.perf_counter() - started
        if exported.get("event") != "done":
            raise AssertionError(json.dumps(exported, indent=2))
        export_path = Path(str(exported["path"]))
        after_first = _argv_count(log_path)
        client.send({"cmd": "export", "id": "mp4-again"})
        again = client.wait_for_end("mp4-again", 30)
    finally:
        client.close()
    if again.get("event") != "done":
        raise AssertionError(json.dumps(again, indent=2))
    duration, width, height = probe_video(export_path)
    preview_duration, preview_width, preview_height = probe_video(preview_path)
    assert (width, height) == (1920, 1080)
    assert abs(duration - 60.0) <= (1.0 / 30.0)
    assert (preview_width, preview_height) == (854, 480)
    assert abs(preview_duration - 60.0) <= (1.0 / 15.0)
    assert export_path.resolve() != preview_path.resolve()
    assert sha256_file(export_path) != sha256_file(preview_path)
    assert "exports" in export_path.parts
    assert "480p15" not in export_path.parts
    assert Path(str(again["path"])).resolve() == export_path.resolve()
    assert after_first == before + 1
    assert _argv_count(log_path) == after_first
    log = log_path.read_text(encoding="utf-8")
    assert "export cache hit" in log
    argv = _argv_with(log_path, "mp4")
    _assert_flags(argv, "mp4", "1920,1080", "30", transparent=False)
    _write(
        PHASE5 / "reference-60-mp4.json",
        {
            "format": "mp4",
            "path": str(export_path),
            "width": width,
            "height": height,
            "duration": duration,
            "seconds": seconds,
            "preview_path": str(preview_path),
            "preview_size": [preview_width, preview_height],
            "hashes_differ": True,
            "second_export_skipped_manim": True,
            "budget": "not a budget result",
            "argv": argv,
        },
    )


@pytest.fixture(scope="module")
def surface() -> dict[str, Path]:
    """The default 3D surface exported once as a transparent WebM and once as a transparent PNG sequence."""
    project = PHASE5 / "surface"
    log_path = PHASE5 / "logs" / "surface.log"
    _reset(project)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    client = start_supervisor(project, log_path, init=True)
    try:
        _ready(client, "warm")
        client.send({"cmd": "edit", "id": "insert", "edit": {"op": "add_three_d_axes"}})
        assert _wait_state(client, "insert")["event"] == "state"
        client.send({"cmd": "edit", "id": "webm-profile", "edit": _profile("webm", 1280, 720, 30, True)})
        assert _wait_state(client, "webm-profile")["event"] == "state"
        client.send({"cmd": "export", "id": "webm"})
        webm_done = client.wait_for_end("webm", 900)
        if webm_done.get("event") != "done":
            raise AssertionError(json.dumps(webm_done, indent=2))
        client.send({"cmd": "edit", "id": "png-profile", "edit": _profile("png_sequence", 1280, 720, 30, True)})
        assert _wait_state(client, "png-profile")["event"] == "state"
        client.send({"cmd": "export", "id": "png"})
        png_done = client.wait_for_end("png", 900)
        if png_done.get("event") != "done":
            raise AssertionError(json.dumps(png_done, indent=2))
    finally:
        client.close()
    return {"webm": Path(str(webm_done["path"])), "frames": Path(str(png_done["path"])), "log": log_path}


def test_surface_transparent_webm_and_png_sequence(surface: dict[str, Path]) -> None:
    webm_path, frames, log_path = surface["webm"], surface["frames"], surface["log"]
    webm_duration, webm_width, webm_height = probe_video(webm_path)
    assert (webm_width, webm_height) == (1280, 720)
    assert abs(webm_duration - 4.0) <= (1.0 / 30.0)
    assert webm_path.suffix == ".webm"
    webm_alpha = _alpha_webm(webm_path)
    pngs = sorted(frames.glob("*.png"))
    assert pngs
    png_alpha = _alpha_png(_late_frame(pngs))
    png_alpha_ok = png_alpha["corner_alpha"] == 0 and png_alpha["opaque_surface"] is True
    _write(
        PHASE5 / "surface-alpha.json",
        {
            "webm": {
                "path": str(webm_path),
                "width": webm_width,
                "height": webm_height,
                "duration": webm_duration,
                "alpha": webm_alpha,
                "alpha_check": _check(_has_alpha(webm_alpha)),
            },
            "png_sequence": {
                "path": str(frames),
                "frames": len(pngs),
                "alpha": png_alpha,
                "alpha_check": _check(png_alpha_ok),
            },
        },
    )
    assert png_alpha["size"] == [1280, 720]
    assert png_alpha_ok
    assert abs(len(pngs) - 120) <= 2
    _assert_flags(_argv_with(log_path, "webm"), "webm", "1280,720", "30", transparent=True)
    _assert_flags(_argv_with(log_path, "png-sequence"), "png-sequence", "1280,720", "30", transparent=True)


def test_transparent_webm_keeps_its_alpha(surface: dict[str, Path]) -> None:
    """D54, G2.5: the alpha the WebM stores, as a libvpx-vp9 decoder such as WebView2's reads it."""
    alpha = _alpha_webm(surface["webm"])
    assert _has_alpha(alpha), f"transparent WebM lost its alpha: {alpha} command={_argv_with(surface['log'], 'webm')}"


def _profile(fmt: str, width: int, height: int, fps: int, transparent: bool) -> dict[str, object]:
    return {
        "op": "set_export_profile",
        "format": fmt,
        "width": width,
        "height": height,
        "fps": fps,
        "transparent": transparent,
    }


def _reset(project: Path) -> None:
    if project.exists():
        shutil.rmtree(project)
    project.mkdir(parents=True)


def _ready(client: SupervisorClient, job_id: str = "status") -> None:
    client.send({"cmd": "status", "id": job_id})
    client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == job_id, 30)


def _open_fixture(client: SupervisorClient, source: Path, project: Path) -> None:
    _ready(client)
    shutil.copyfile(source, project / "project.json")
    client.send({"cmd": "open_project", "id": "open", "path": str(project)})
    opened = _wait_state(client, "open")
    if opened.get("event") != "state":
        raise AssertionError(json.dumps(opened, indent=2))


def _wait_state(client: SupervisorClient, job_id: str, timeout: float = 30) -> dict[str, object]:
    return client.wait_for(
        lambda event: event.get("jobId") == job_id and event.get("event") in {"state", "rejected", "error"},
        timeout,
    )


def _argv_count(log_path: Path) -> int:
    if not log_path.is_file():
        return 0
    return log_path.read_text(encoding="utf-8", errors="replace").count("argv: ")


def _argv_with(log_path: Path, flag: str) -> list[str]:
    found: list[str] | None = None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = "argv: "
        if marker not in line:
            continue
        argv = json.loads(line.split(marker, 1)[1])
        if isinstance(argv, list) and flag in argv:
            found = argv
    if found is None:
        raise AssertionError(f"{flag} was not in {log_path}")
    return found


def _assert_flags(argv: list[str], fmt: str, resolution: str, fps: str, *, transparent: bool) -> None:
    assert argv[argv.index("--format") + 1] == fmt
    assert argv[argv.index("-r") + 1] == resolution
    assert argv[argv.index("--fps") + 1] == fps
    assert argv[argv.index("--renderer") + 1] == "cairo"
    assert ("--transparent" in argv) is transparent
    joined = " ".join(argv)
    for token in FORBIDDEN:
        assert token not in joined


def _alpha_png(path: Path) -> dict[str, object]:
    from PIL import Image

    with Image.open(path) as image:
        return _alpha_image(image.convert("RGBA"), path)


def _alpha_webm(path: Path) -> dict[str, object]:
    """The alpha of the WebM's last frame, decoded with libvpx-vp9 (G2.5).

    A VP9 WebM keeps its alpha in a separate block stream, which the track tag
    ``alpha_mode`` announces. FFmpeg's own vp9 decoder ignores that stream and
    reports every frame opaque; only libvpx-vp9 decodes it.
    """
    import av
    from PIL import Image

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        decoder = av.CodecContext.create("libvpx-vp9", "r")
        last = None
        # The last packet demux yields is empty and flushes the decoder.
        for packet in container.demux(stream):
            for frame in decoder.decode(packet):
                last = frame
        if last is None:
            raise AssertionError(f"no frames in {path}")
        image = Image.fromarray(last.to_ndarray(format="rgba"), mode="RGBA")
        result = _alpha_image(image, path)
        result["alpha_mode"] = stream.metadata.get("alpha_mode")
        result["pixel_format"] = last.format.name
        result["codec"] = decoder.name
        return result


def _has_alpha(alpha: dict[str, object]) -> bool:
    """A transparent background around an opaque surface, from a stream that says it carries alpha."""
    return alpha["alpha_mode"] == "1" and alpha["corner_alpha"] == 0 and alpha["opaque_surface"] is True


def _late_frame(pngs: list[Path]) -> Path:
    """A frame from the end of the scene: the surface enters in the second 2 s beat, so it shows the fill."""
    return pngs[min(len(pngs) - 1, int(len(pngs) * 0.9))]


def _check(passed: bool) -> str:
    return "passed" if passed else "failed"


def _alpha_image(image, path: Path) -> dict[str, object]:
    width, height = image.size
    corner = image.getpixel((0, 0))
    surface_opaque = 0
    surface_partial = 0
    for pixel in image.get_flattened_data():
        red, green, blue, alpha = pixel
        if green > 140 and blue > 140 and (green - red) > 30:
            if alpha == 255:
                surface_opaque += 1
            elif alpha > 0:
                surface_partial += 1
    return {
        "frame": str(path),
        "size": [width, height],
        "corner_alpha": corner[3] if isinstance(corner, tuple) else None,
        "surface_opaque_pixels": surface_opaque,
        "surface_partial_pixels": surface_partial,
        "opaque_surface": surface_opaque > 1000 and surface_opaque > surface_partial,
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
