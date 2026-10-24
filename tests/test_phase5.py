"""Phase 5 export presets, notices, and cache identity.

Render, alpha, and offline-bundle checks live in the marked modules. These
tests do not start Manim.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from mathimator_worker.argv import FORBIDDEN_TOKENS, build_manim_argv
from mathimator_worker.codegen import generate
from mathimator_worker.document import (
    dumps_project,
    export_artifact_name,
    export_folder_name,
    export_key,
    load_text,
)
from mathimator_worker.document_error import DocumentError
from mathimator_worker.session import Session
from mathimator_worker.supervisor import find_export_output
from tests.support import REPO, start_supervisor, write_fake_interpreter

FIXTURES = REPO / "tests" / "fixtures"
JOURNEY = FIXTURES / "journey-a" / "project.json"
REFERENCE = FIXTURES / "reference-60" / "project.json"


def test_export_argv_matrix_matches_manim_022_flags() -> None:
    cases = (
        ("mp4", 1920, 1080, 30, False, "mp4", False),
        ("gif", 854, 480, 15, False, "gif", False),
        ("webm", 1920, 1080, 30, False, "webm", False),
        ("webm", 1280, 720, 30, True, "webm", True),
        ("png_sequence", 1920, 1080, 30, False, "png-sequence", False),
        ("png_sequence", 1280, 720, 30, True, "png-sequence", True),
    )
    for fmt, width, height, fps, transparent, flag, expect_transparent in cases:
        argv = build_manim_argv(
            "/opt/py/bin/python",
            "export",
            width=width,
            height=height,
            fps=fps,
            fmt=fmt,
            transparent=transparent,
        )
        assert argv[argv.index("--format") + 1] == flag
        assert argv[argv.index("-r") + 1] == f"{width},{height}"
        assert argv[argv.index("--fps") + 1] == str(fps)
        assert argv[argv.index("--renderer") + 1] == "cairo"
        assert ("--transparent" in argv) is expect_transparent
        joined = " ".join(argv)
        for token in FORBIDDEN_TOKENS:
            assert token not in argv
        assert "opengl" not in joined
        assert "--preview" not in joined
        assert "--disable_caching" not in joined


def test_mp4_and_gif_cannot_be_transparent() -> None:
    session = Session(load_text(JOURNEY.read_text(encoding="utf-8")))
    with pytest.raises(DocumentError, match="MP4 cannot store transparency."):
        session.edit(_profile("mp4", 1920, 1080, 30, True))
    with pytest.raises(DocumentError, match="GIF cannot store an alpha channel."):
        session.edit(_profile("gif", 854, 480, 15, True))
    with pytest.raises(ValueError, match="mp4 cannot be transparent"):
        build_manim_argv(
            "python",
            "export",
            fmt="mp4",
            transparent=True,
        )
    with pytest.raises(ValueError, match="gif cannot be transparent"):
        build_manim_argv(
            "python",
            "export",
            fmt="gif",
            transparent=True,
        )


def test_webm_and_png_sequence_keep_transparency() -> None:
    session = Session(load_text(JOURNEY.read_text(encoding="utf-8")))
    session.edit(_profile("webm", 1280, 720, 30, True))
    assert session.project.export_profile.format == "webm"
    assert session.project.export_profile.transparent is True
    session.edit(_profile("png_sequence", 1280, 720, 30, True))
    profile = session.project.export_profile
    assert (profile.format, profile.width, profile.height, profile.fps, profile.transparent) == (
        "png_sequence",
        1280,
        720,
        30,
        True,
    )
    assert session.project.preview_profile.transparent is False
    assert (session.project.preview_profile.width, session.project.preview_profile.height) == (854, 480)


def test_export_profile_does_not_change_generated_python() -> None:
    session = Session(load_text(REFERENCE.read_text(encoding="utf-8")))
    before = generate(session.project)
    session.edit(_profile("gif", 854, 480, 15, False))
    after = generate(session.project)
    assert after == before
    assert "MathTex" in after
    assert "Typst(" not in after
    assert "TypstMath" not in after
    for token in ("fps", "1920", "854", "--format"):
        assert token not in after


def test_preview_mp4_is_not_selected_for_gif_or_a_1080p_export(tmp_path: Path) -> None:
    media = tmp_path / "media"
    preview = media / "videos" / "scene" / "480p15" / "MainScene.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"preview")
    gif = media / "videos" / "scene" / "480p15" / "MainScene_ManimCE_v0.22.0.gif"
    gif.write_bytes(b"gif")
    hd = media / "videos" / "scene" / "1080p30" / "MainScene.mp4"
    hd.parent.mkdir(parents=True)
    hd.write_bytes(b"hd")
    assert find_export_output(media, "scene", "480p15", "gif") == gif
    assert find_export_output(media, "scene", "1080p30", "mp4") == hd
    assert find_export_output(media, "scene", "1080p30", "gif") is None
    assert find_export_output(media, "scene", "480p15", "mp4") == preview


def test_gif_and_webm_exports_are_not_refused(tmp_path: Path) -> None:
    fake = write_fake_interpreter(tmp_path / "fake-manim.py", 30)
    gif_argv, gif_log = _export_with_fake(
        tmp_path / "gif",
        tmp_path / "gif.log",
        fake,
        _profile("gif", 854, 480, 15, False),
        "gif",
    )
    webm_argv, webm_log = _export_with_fake(
        tmp_path / "webm",
        tmp_path / "webm.log",
        fake,
        _profile("webm", 1920, 1080, 30, True),
        "webm",
    )
    assert gif_argv[gif_argv.index("--format") + 1] == "gif"
    assert "--transparent" not in gif_argv
    assert webm_argv[webm_argv.index("--format") + 1] == "webm"
    assert "--transparent" in webm_argv
    assert "This phase exports MP4 only." not in gif_log
    assert "This phase exports MP4 only." not in webm_log


def test_unchanged_gif_export_key_does_not_start_manim(tmp_path: Path) -> None:
    project = tmp_path / "gif-hit"
    shutil.copytree(FIXTURES / "journey-a", project)
    session = Session(load_text((project / "project.json").read_text(encoding="utf-8")))
    session.edit(_profile("gif", 854, 480, 15, False))
    (project / "project.json").write_text(dumps_project(session.project), encoding="utf-8")
    loaded = session.project
    folder = project / ".mathimator" / "exports" / export_folder_name(loaded)
    folder.mkdir(parents=True)
    artifact = folder / export_artifact_name("gif")
    artifact.write_bytes(b"already-exported")
    manifest = {
        "exportKey": export_key(loaded),
        "manimVersion": "0.22.0",
        "generatorVersion": 2,
        "file": "scene.gif",
        "profile": {
            "format": "gif",
            "width": 854,
            "height": 480,
            "fps": 15,
            "transparent": False,
        },
    }
    (folder / "export.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    log_path = tmp_path / "gif-hit.log"
    fake = write_fake_interpreter(tmp_path / "fake-manim.py", 30)
    client = start_supervisor(project, log_path, python=fake)
    try:
        _ready(client)
        client.send({"cmd": "export", "id": "again"})
        done = client.wait_for(
            lambda event: event.get("jobId") == "again" and event.get("event") in {"done", "error"},
            10,
        )
        assert done["event"] == "done"
        assert done["path"] == str(artifact)
    finally:
        client.close()
    assert "argv:" not in log_path.read_text(encoding="utf-8")
    assert "export cache hit" in log_path.read_text(encoding="utf-8")


def test_notices_name_manim_typst_and_tinytex() -> None:
    text = (REPO / "desktop" / "src" / "notices.ts").read_text(encoding="utf-8")
    assert "Manim" in text
    assert "Typst" in text
    assert "TinyTeX" in text
    assert "MIT" in text
    assert "WebView2" in text
    panel = (REPO / "desktop" / "src" / "exportProfile.ts").read_text(encoding="utf-8")
    assert "MP4 cannot store transparency." in panel
    assert "GIF cannot store an alpha channel." in panel
    assert "854" in panel and "480" in panel and "15" in panel
    assert "1920" in panel and "1080" in panel and "30" in panel


def _profile(fmt: str, width: int, height: int, fps: int, transparent: bool) -> dict[str, object]:
    return {
        "op": "set_export_profile",
        "format": fmt,
        "width": width,
        "height": height,
        "fps": fps,
        "transparent": transparent,
    }


def _ready(client) -> None:
    client.send({"cmd": "status", "id": "status"})
    client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == "status", 10)


def _export_with_fake(
    project: Path,
    log_path: Path,
    fake: Path,
    edit: dict[str, object],
    flag: str,
) -> tuple[list[str], str]:
    shutil.copytree(FIXTURES / "journey-a", project)
    client = start_supervisor(project, log_path, python=fake)
    try:
        _ready(client)
        client.send({"cmd": "edit", "id": "profile", "edit": edit})
        assert client.wait_for(lambda event: event.get("jobId") == "profile", 10)["event"] == "state"
        client.send({"cmd": "export", "id": "export"})
        argv = _wait_argv(log_path, flag)
        # Cancelled before the worker closes, so no fake Manim outlives the test (F028).
        client.send({"cmd": "cancel", "id": "cancel", "jobId": "export"})
        ended = client.wait_for(lambda event: event.get("jobId") == "export" and event.get("event") == "error", 10)
        assert ended["code"] == "cancelled"
    finally:
        client.close()
    return argv, log_path.read_text(encoding="utf-8")


def _wait_argv(log_path: Path, flag: str) -> list[str]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if log_path.is_file():
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                marker = "argv: "
                if marker not in line:
                    continue
                argv = json.loads(line.split(marker, 1)[1])
                if isinstance(argv, list) and flag in argv:
                    return argv
        time.sleep(0.05)
    raise TimeoutError(log_path.read_text(encoding="utf-8") if log_path.is_file() else "no log")
