"""Timed Cairo renders for the phase 4 sample scenes.

Cold preview cache means ``videos/`` is removed before the timed insert.
The worker is already answering ``status``. The clock includes the 300 ms
preview debounce and stops when both partial movies exist. The Windows 20 s
budget is not judged here.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from mathimator_worker.codegen import generate
from mathimator_worker.document import dumps_project, load_text
from tests.support import REPO, SupervisorClient, media_dir, probe_video, scene_path, start_supervisor

pytestmark = pytest.mark.render

PHASE4 = REPO / "out" / "phase4"


def test_phase4_surface_curve_formula_and_png_sequence() -> None:
    surface_project = PHASE4 / "surface"
    formula_project = PHASE4 / "formula"
    if PHASE4.exists():
        shutil.rmtree(PHASE4)
    log_dir = PHASE4 / "logs"
    log_dir.mkdir(parents=True)
    surface_log = log_dir / "surface.log"
    client = start_supervisor(surface_project, surface_log, init=True)
    try:
        _ready(client, "warm")
        videos = media_dir(surface_project) / "videos"
        if videos.exists():
            shutil.rmtree(videos)
        started = time.perf_counter()
        client.send({"cmd": "edit", "id": "insert", "edit": {"op": "add_three_d_axes"}})
        client.wait_for(lambda event: event.get("jobId") == "insert" and event.get("event") == "state", 30)
        time.sleep(0.3)
        client.send({"cmd": "render_preview", "id": "surface-preview"})
        second = client.wait_for(
            lambda event: event.get("jobId") == "surface-preview"
            and event.get("event") == "beat_ready"
            and event.get("beatIndex") == 1,
            300,
        )
        insert_seconds = time.perf_counter() - started
        done = client.wait_for(
            lambda event: event.get("jobId") == "surface-preview" and event.get("event") in {"done", "error"},
            300,
        )
        if done.get("event") != "done":
            raise AssertionError(json.dumps(done, indent=2))
        preview_path = Path(str(done["path"]))
        partials = _beats(client, "surface-preview")
        kept_preview = PHASE4 / "surface-preview.mp4"
        shutil.copyfile(preview_path, kept_preview)
        preview_path = kept_preview
        client.send(
            {
                "cmd": "edit",
                "id": "profile",
                "edit": {
                    "op": "set_export_profile",
                    "format": "png_sequence",
                    "width": 1280,
                    "height": 720,
                    "fps": 30,
                    "transparent": True,
                },
            }
        )
        profiled = client.wait_for(lambda event: event.get("jobId") == "profile", 30)
        assert profiled["event"] == "state"
        client.send({"cmd": "export", "id": "png"})
        exported = client.wait_for(
            lambda event: event.get("jobId") == "png" and event.get("event") in {"done", "error"},
            600,
        )
        if exported.get("event") != "done":
            raise AssertionError(json.dumps(exported, indent=2))
        frames_dir = Path(str(exported["path"]))
        client.send({"cmd": "edit", "id": "curve", "edit": {"op": "set_three_d_mode", "mode": "curve"}})
        client.wait_for(lambda event: event.get("jobId") == "curve" and event.get("event") == "state", 30)
        time.sleep(0.3)
        client.send({"cmd": "render_preview", "id": "curve-preview"})
        curve_done = client.wait_for(
            lambda event: event.get("jobId") == "curve-preview" and event.get("event") in {"done", "error"},
            300,
        )
        if curve_done.get("event") != "done":
            raise AssertionError(json.dumps(curve_done, indent=2))
        curve_path = Path(str(curve_done["path"]))
    finally:
        client.close()

    assert preview_path.is_file()
    assert len(partials) >= 2
    partial_paths = [Path(str(event["path"])) for event in partials]
    assert all(path.is_file() for path in partial_paths)
    assert all("partial_movie_files" in path.parts for path in partial_paths)
    duration, width, height = probe_video(preview_path)
    assert (width, height) == (854, 480)
    assert abs(duration - 4.0) < 0.2
    surface_argv = _argv_from_log(surface_log, "surface-preview")
    _assert_cairo(surface_argv)
    assert surface_argv[surface_argv.index("--format") + 1] == "mp4"
    assert "--transparent" not in surface_argv

    pngs = sorted(frames_dir.glob("*.png"))
    assert len(pngs) > 1
    # The surface entrance is the second 2 s beat, so a late frame shows the fill.
    late = pngs[min(len(pngs) - 1, int(len(pngs) * 0.9))]
    alpha = _alpha_check(late)
    assert alpha["size"] == [1280, 720]
    assert alpha["corner_alpha"] == 0
    assert alpha["opaque_surface"] is True
    export_argv = _argv_from_log(surface_log, None)
    png_commands = [argv for argv in export_argv if "png-sequence" in argv]
    assert png_commands
    _assert_cairo(png_commands[-1])
    assert "--transparent" in png_commands[-1]
    assert png_commands[-1][png_commands[-1].index("-r") + 1] == "1280,720"

    curve_duration, curve_width, curve_height = probe_video(curve_path)
    assert curve_path.is_file()
    assert (curve_width, curve_height) == (854, 480)
    assert abs(curve_duration - 4.0) < 0.2
    curve_source = generate(load_text((surface_project / "project.json").read_text(encoding="utf-8")))
    assert "ParametricFunction" in curve_source
    assert "begin_ambient_camera_rotation" not in curve_source

    formula_log = log_dir / "formula.log"
    formula = start_supervisor(formula_project, formula_log, init=True)
    try:
        _ready(formula, "formula-warm")
        formula.send({"cmd": "edit", "id": "add-formula", "edit": {"op": "add_formula_transform"}})
        formula.wait_for(lambda event: event.get("jobId") == "add-formula" and event.get("event") == "state", 30)
        time.sleep(0.3)
        formula.send({"cmd": "render_preview", "id": "formula-preview"})
        formula_done = formula.wait_for(
            lambda event: event.get("jobId") == "formula-preview" and event.get("event") in {"done", "error"},
            300,
        )
        if formula_done.get("event") != "done":
            raise AssertionError(json.dumps(formula_done, indent=2))
        formula_path = Path(str(formula_done["path"]))
    finally:
        formula.close()
    formula_source = (scene_path(formula_project)).read_text(encoding="utf-8")
    assert "TransformMatchingTex" in formula_source
    formula_duration, formula_width, formula_height = probe_video(formula_path)
    assert formula_path.is_file()
    assert (formula_width, formula_height) == (854, 480)
    assert abs(formula_duration - 4.0) < 0.2

    _write(
        PHASE4 / "timings.json",
        {
            "insert_seconds": insert_seconds,
            "includes_300ms_debounce": True,
            "both_partials_exist": True,
            "partials": [str(path) for path in partial_paths],
            "preview_path": str(preview_path),
            "preview_duration": duration,
            "preview_size": [width, height],
            "png_frames_dir": str(frames_dir),
            "png_count": len(pngs),
            "alpha": alpha,
            "curve_preview": str(curve_path),
            "curve_duration": curve_duration,
            "formula_preview": str(formula_path),
            "formula_duration": formula_duration,
            "windows_20s_budget": "not judged",
            "project_bytes": dumps_project(load_text((surface_project / "project.json").read_text(encoding="utf-8"))),
        },
    )


def _alpha_check(path: Path) -> dict[str, object]:
    """Background alpha is 0. Lit surface faces stay opaque.

    Cairo flat-shades ``#58C4DD``, so face pixels are cyan and brighter than
    the stored fill. Antialiased edges can have a partial alpha. The fill
    itself is the opaque majority of those cyan pixels.
    """
    from PIL import Image

    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        width, height = rgba.size
        corner = rgba.getpixel((0, 0))
        surface_opaque = 0
        surface_partial = 0
        for pixel in rgba.get_flattened_data():
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


def _ready(client: SupervisorClient, job_id: str) -> None:
    client.send({"cmd": "status", "id": job_id})
    client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == job_id, 30)


def _beats(client: SupervisorClient, job_id: str) -> list[dict[str, object]]:
    return [
        event
        for event in client.events
        if event.get("event") == "beat_ready" and event.get("jobId") == job_id
    ]


def _argv_from_log(log_path: Path, job_hint: str | None) -> list[str] | list[list[str]]:
    commands: list[list[str]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = "argv: "
        if marker in line:
            parsed = json.loads(line.split(marker, 1)[1])
            if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                commands.append(parsed)
    if job_hint == "surface-preview":
        for command in commands:
            if command[command.index("--format") + 1] == "mp4":
                return command
    return commands


def _assert_cairo(argv: list[str]) -> None:
    assert argv[argv.index("--renderer") + 1] == "cairo"
    assert "--seed" in argv
    joined = " ".join(argv)
    assert "opengl" not in joined
    assert "--live-preview" not in joined
    assert "--preview" not in joined


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
