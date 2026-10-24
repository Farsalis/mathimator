"""Scene code and cache safety (fix-plan S13 to S16 and S41, owner answer Q3).

The generated-code folder, the startup project path, PNG-sequence frames, the
interrupted-run marker, and the completeness check for partial movies. No test
here starts Manim. Jobs run the ``fake_manim`` stand-in (``support.FakeManim``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import struct
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

from mathimator_worker import clip_check
from mathimator_worker.clip_check import ClipChecker, clip_is_complete
from mathimator_worker.codegen import generate
from mathimator_worker.document import dumps_project, export_artifact_name, export_folder_name, load_text
from mathimator_worker.progress import ManimProgress
from mathimator_worker.session import Session
from mathimator_worker.supervisor import Supervisor, _Job, _remove_tree, partial_movie_dir, png_frames_dir
from tests.support import REPO, FakeManim, complete_clip, ebml_element, iso_box, video_render, write_clip

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"
SUFFIXES = (".mp4", ".mov", ".webm")
_PNG_PROFILE = {"format": "png_sequence", "width": 1280, "height": 720, "fps": 30, "transparent": False}
_WEBM_PROFILE = {"format": "webm", "width": 1920, "height": 1080, "fps": 30, "transparent": False}

# Completeness check (owner answer Q3)


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_complete_clips_pass(tmp_path: Path, suffix: str) -> None:
    assert clip_is_complete(write_clip(tmp_path / f"clip{suffix}"))


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_every_truncation_of_a_clip_fails(tmp_path: Path, suffix: str) -> None:
    data = complete_clip(suffix)
    clip = tmp_path / f"clip{suffix}"
    accepted = []
    for length in range(len(data)):
        clip.write_bytes(data[:length])
        if clip_is_complete(clip):
            accepted.append(length)
    assert accepted == []


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_clips_that_the_muxer_never_closed_fail(tmp_path: Path, suffix: str) -> None:
    clip = tmp_path / f"clip{suffix}"
    clip.write_bytes(_cut_off_clip(suffix))
    assert not clip_is_complete(clip)


@pytest.mark.parametrize("suffix", [".mp4", ".mov"])
def test_a_moov_that_still_has_its_size_placeholder_fails(tmp_path: Path, suffix: str) -> None:
    # A large moov is flushed in parts. A kill after the first part leaves the
    # size 0 that FFmpeg fills in only when it closes the file.
    start = iso_box(b"ftyp", b"isom" + bytes(4)) + iso_box(b"free") + iso_box(b"mdat", bytes(64))
    unsized_moov = struct.pack(">I4s", 0, b"moov") + iso_box(b"mvhd", bytes(100))[:40]
    clip = tmp_path / f"clip{suffix}"
    clip.write_bytes(start + unsized_moov)
    assert not clip_is_complete(clip)


def test_clip_check_reads_64_bit_box_sizes(tmp_path: Path) -> None:
    payload = bytes(64)
    large_mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)) + payload
    clip = tmp_path / "large.mp4"
    clip.write_bytes(iso_box(b"ftyp", b"isom" + bytes(4)) + large_mdat + iso_box(b"moov"))
    assert clip_is_complete(clip)


def test_clip_check_refuses_extra_bytes_and_other_files(tmp_path: Path) -> None:
    for suffix in SUFFIXES:
        clip = tmp_path / f"padded{suffix}"
        clip.write_bytes(complete_clip(suffix) + b"\x00junk")
        assert not clip_is_complete(clip), suffix
    without_moov = tmp_path / "no-moov.mp4"
    without_moov.write_bytes(iso_box(b"ftyp", b"isom" + bytes(4)) + iso_box(b"mdat", bytes(64)))
    assert not clip_is_complete(without_moov)
    without_segment = tmp_path / "no-segment.webm"
    without_segment.write_bytes(ebml_element(0x1A45DFA3, ebml_element(0x4282, b"webm")))
    assert not clip_is_complete(without_segment)
    gif = tmp_path / "MainScene.gif"
    gif.write_bytes(complete_clip(".mp4"))
    assert not clip_is_complete(gif)
    assert not clip_is_complete(tmp_path / "missing.mp4")
    assert not clip_is_complete(tmp_path)
    assert not clip_is_complete(tmp_path / "x\u0000y.mp4")


def test_clip_checker_reads_a_clip_again_only_when_it_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[Path] = []

    def counting(path: Path) -> bool:
        reads.append(path)
        return clip_is_complete(path)

    monkeypatch.setattr(clip_check, "clip_is_complete", counting)
    clip = write_clip(tmp_path / "clip.mp4")
    checker = ClipChecker()
    assert checker.complete(clip)
    assert checker.complete(clip)
    assert reads == [clip]
    clip.write_bytes(_cut_off_clip(".mp4"))
    assert not checker.complete(clip)
    assert not checker.complete(tmp_path / "missing.mp4")
    assert not checker.complete(tmp_path / "x\u0000y.mp4")


def test_progress_stops_at_an_incomplete_partial(tmp_path: Path) -> None:
    media = tmp_path / "media"
    folder = partial_movie_dir(media, "scene", "480p15")
    folder.mkdir(parents=True)
    first = write_clip(folder / "a.mp4")
    second = folder / "b.mp4"
    second.write_bytes(_cut_off_clip(".mp4"))
    third = write_clip(folder / "c.mp4")
    progress = ManimProgress(media)
    events = progress.feed(
        "".join(
            f"Animation {index} : Partial movie file written in '{path}'\n"
            for index, path in enumerate([first, second, third])
        )
    )
    assert events == [(0, first)]
    assert progress.incomplete == [second]
    assert progress.feed("\n") == []
    assert progress.beats == {0: first}
    assert progress.incomplete == [second]


def test_an_incomplete_partial_never_makes_its_beat_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path / "index")
    session = Session(load_text((project / "project.json").read_text(encoding="utf-8")))
    session.mark_rendered("preview")
    cache = project / ".mathimator" / "cache"
    folder = partial_movie_dir(cache / "media", "scene", "480p15")
    folder.mkdir(parents=True)
    complete = write_clip(folder / ("a" * 64 + ".mp4"))
    cut_off = folder / ("b" * 64 + ".mp4")
    cut_off.write_bytes(_cut_off_clip(".mp4"))
    beats = [
        {
            "id": beat.id,
            "index": index,
            "derivedStart": float(index),
            "duration": 1.0,
            "partial": str(path),
            "lastGood": None,
            "fresh": True,
        }
        for index, (beat, path) in enumerate(zip(session.project.scene.beats, [complete, cut_off], strict=False))
    ]
    (cache / "preview-index.json").write_text(json.dumps({"output": None, "beats": beats}), encoding="utf-8")
    (cache / "rendered.json").write_text(json.dumps(session.rendered_payload()), encoding="utf-8")
    supervisor, _events = _supervisor(project, monkeypatch)
    assert supervisor._index is not None
    first, second = supervisor._index.beats[:2]
    assert first.fresh and first.partial == str(complete)
    assert not second.fresh and second.partial is None


def test_a_cut_off_cached_clip_is_removed_before_manim_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    supervisor, events = _supervisor(_project(tmp_path / "cached"), monkeypatch)
    folder = partial_movie_dir(supervisor.media_dir, "scene", "480p15")
    folder.mkdir(parents=True)
    kept = _aged(write_clip(folder / ("c" * 64 + ".mp4")), time.time() - 600)
    cut_off = folder / ("d" * 64 + ".mp4")
    cut_off.write_bytes(_cut_off_clip(".mp4"))
    _aged(cut_off, time.time() - 600)
    fake_manim.plan(video_render(supervisor.media_dir, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, "render_preview", "preview-1")
    assert _terminal(events, "preview-1")["event"] == "done"
    seen = fake_manim.seen()["files"]
    assert kept.relative_to(supervisor.media_dir).as_posix() in seen
    assert cut_off.relative_to(supervisor.media_dir).as_posix() not in seen
    assert kept.is_file()
    assert not cut_off.exists()


def test_an_export_that_used_an_incomplete_clip_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "webm", export=_WEBM_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    clips = [complete_clip(".webm"), _cut_off_clip(".webm"), complete_clip(".webm"), complete_clip(".webm")]
    files = video_render(supervisor.media_dir, "1080p30", clips, suffix=".webm")
    fake_manim.plan(files)
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["event"] == "error"
    assert "incomplete clip" in str(failed["message"])
    partials = [path for path in files if path.suffix == ".webm" and "partial_movie_files" in path.parts]
    assert [path.exists() for path in partials] == [True, False, True, True]
    assert not (project / ".mathimator" / "exports").exists()


def test_an_incomplete_clip_in_another_quality_folder_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "elsewhere")
    supervisor, events = _supervisor(project, monkeypatch)
    folder = partial_movie_dir(supervisor.media_dir, "scene", "480p15")
    folder.mkdir(parents=True)
    digest = "9" * 64
    cut_off = folder / f"{digest}.mp4"
    cut_off.write_bytes(_cut_off_clip(".mp4"))
    files = video_render(supervisor.media_dir, "1080p30", [complete_clip(".mp4")] * 4)
    fake_manim.plan(files, [f"Animation 0 : Using cached data (hash : {digest})"])
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["message"] == f"manim used an incomplete clip {cut_off.name}; it was removed, so render again"
    assert not cut_off.exists()
    assert all(path.is_file() for path in files)
    assert not (project / ".mathimator" / "exports").exists()


def test_png_export_ignores_the_segment_list_of_another_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "frames", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    folder = partial_movie_dir(supervisor.media_dir, "scene", "480p15")
    folder.mkdir(parents=True)
    cut_off = folder / ("a" * 64 + ".mp4")
    cut_off.write_bytes(_cut_off_clip(".mp4"))
    (folder / "partial_movie_file_list.txt").write_text(f"file 'file:{cut_off.as_posix()}'\n", encoding="utf-8")
    frames = png_frames_dir(supervisor.media_dir, "scene")
    fake_manim.plan(_png_render(frames, 3), _wrapped_images_ready(3, frames))
    _run(supervisor, "export", "export-1")
    assert _terminal(events, "export-1")["event"] == "done"
    assert not [event for event in events if event["event"] == "beat_ready"]
    # The next preview of that quality removes it before Manim starts.
    assert cut_off.is_file()


# Codegen folder (F170)


def test_a_render_replaces_a_planted_codegen_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, _events = _supervisor(_project(tmp_path / "shared"), monkeypatch)
    scene = supervisor.scene_path
    scene.parent.mkdir(parents=True)
    scene.write_text("REAL = True\n", encoding="utf-8")
    planted = tmp_path / "planted.py"
    planted.write_text("PLANTED = True\n", encoding="utf-8")
    py_compile.compile(
        str(planted),
        cfile=importlib.util.cache_from_source(str(scene)),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    (scene.parent / "numpy.py").write_text("raise SystemExit('shadowed')\n", encoding="utf-8")
    # Python runs the planted bytecode instead of the scene.py beside it.
    assert hasattr(_import_file(scene), "PLANTED")
    job = _job("preview")
    with supervisor._doc_lock:
        assert supervisor._prepare_job_locked(job)
    assert [path.name for path in scene.parent.iterdir()] == ["scene.py"]
    assert supervisor.session is not None
    assert scene.read_text(encoding="utf-8") == generate(supervisor.session.project)


# Startup path (G4.1, G4.10)


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 aliases exist only on Windows")
def test_startup_resolves_a_short_path_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "a project folder with a long name"
    folder.mkdir()
    alias = _short_path(folder)
    if "~" not in alias.name:
        pytest.skip("this volume does not create 8.3 names")
    supervisor, events = _supervisor(alias, monkeypatch)
    assert supervisor.project == folder
    supervisor.handle_line(json.dumps({"cmd": "get_state", "id": "state"}))
    assert events[-1]["projectPath"] == str(folder)
    job = _job("preview")
    with supervisor._doc_lock:
        assert supervisor._prepare_job_locked(job)
    assert "~" not in job.argv[job.argv.index("--media_dir") + 1]


# Interrupted-run marker (G1.1, G1.2)


def test_startup_sweeps_media_left_by_an_interrupted_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path / "crashed")
    started = time.time() - 120
    marker = _write_marker(project, json.dumps({"started": started}))
    kept, left = _interrupted_media(project / ".mathimator" / "cache" / "media", started)
    supervisor, _events = _supervisor(project, monkeypatch)
    assert kept.is_file()
    assert [path.exists() for path in left] == [False] * len(left)
    assert not marker.exists()
    assert "removed unfinished media" in supervisor.log_path.read_text(encoding="utf-8")


def test_a_marker_that_cannot_be_read_sweeps_from_its_mtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path / "garbled")
    started = time.time() - 120
    marker = _aged(_write_marker(project, '{"started": '), started)
    kept, left = _interrupted_media(project / ".mathimator" / "cache" / "media", started)
    _supervisor(project, monkeypatch)
    assert kept.is_file()
    assert [path.exists() for path in left] == [False] * len(left)
    assert not marker.exists()


def test_open_project_sweeps_an_interrupted_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, events = _supervisor(tmp_path / "first", monkeypatch)
    project = _project(tmp_path / "second")
    started = time.time() - 120
    marker = _write_marker(project, json.dumps({"started": started}))
    kept, left = _interrupted_media(project / ".mathimator" / "cache" / "media", started)
    supervisor.handle_line(json.dumps({"cmd": "open_project", "id": "open", "path": str(project)}))
    assert events[-1]["event"] == "state"
    assert kept.is_file()
    assert [path.exists() for path in left] == [False] * len(left)
    assert not marker.exists()


def test_open_project_leaves_a_running_export_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "busy")
    supervisor, events = _supervisor(project, monkeypatch)
    files = video_render(supervisor.media_dir, "1080p30", [complete_clip(".mp4")] * 4)
    fake_manim.plan(files, hold=True)
    supervisor.handle_line(json.dumps({"cmd": "export", "id": "export-1"}))
    job = supervisor._current
    assert job is not None
    try:
        fake_manim.wait_until_held()
        supervisor.handle_line(json.dumps({"cmd": "open_project", "id": "open", "path": str(project)}))
        # The open is refused before it can sweep the export's media (D22).
        assert (events[-1]["event"], events[-1]["code"]) == ("rejected", "export_running")
        assert supervisor.run_marker.is_file()
        assert all(path.is_file() for path in files)
    finally:
        fake_manim.release()
        job.thread.join(timeout=60)
    assert not job.thread.is_alive()
    assert _terminal(events, "export-1")["event"] == "done"
    assert not supervisor.run_marker.exists()


def test_reopening_a_folder_leaves_a_preview_that_outlived_its_stop_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "busy")
    supervisor, events = _supervisor(project, monkeypatch)
    # A Manim that is still alive when the 1 s the switch waits for it is over (C8.3).
    monkeypatch.setattr(supervisor, "_kill_process", lambda job: None)
    files = video_render(supervisor.media_dir, "480p15", [complete_clip(".mp4")] * 4)
    fake_manim.plan(files, hold=True)
    supervisor.handle_line(json.dumps({"cmd": "render_preview", "id": "preview-1"}))
    job = supervisor._current
    assert job is not None
    try:
        fake_manim.wait_until_held()
        supervisor.handle_line(json.dumps({"cmd": "open_project", "id": "open", "path": str(project)}))
        # The bind does not sweep the media of a run that is still writing it.
        assert [event["event"] for event in events if event.get("jobId") == "open"] == ["state"]
        assert supervisor.run_marker.is_file()
        assert all(path.is_file() for path in files)
    finally:
        fake_manim.release()
        job.thread.join(timeout=60)
    assert not job.thread.is_alive()
    assert _terminal(events, "preview-1")["code"] == "cancelled"


def test_the_run_marker_exists_only_while_manim_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    supervisor, events = _supervisor(_project(tmp_path / "preview"), monkeypatch)
    fake_manim.plan(video_render(supervisor.media_dir, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, "render_preview", "preview-1")
    assert _terminal(events, "preview-1")["event"] == "done"
    assert fake_manim.seen()["marker"] is True
    assert not supervisor.run_marker.exists()
    assert supervisor._index is not None
    assert [beat.fresh for beat in supervisor._index.beats] == [True] * 4


@pytest.mark.parametrize(
    ("command", "quality", "suffix"),
    [("render_preview", "480p15", ".mp4"), ("export", "1080p30", ".webm")],
)
def test_a_failed_run_discards_its_cut_off_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim, command: str, quality: str, suffix: str
) -> None:
    project = _project(tmp_path / "failed", export=_WEBM_PROFILE if suffix == ".webm" else None)
    supervisor, events = _supervisor(project, monkeypatch)
    folder = partial_movie_dir(supervisor.media_dir, "scene", quality)
    finished = folder / ("e" * 64 + suffix)
    cut_off = folder / ("f" * 64 + suffix)
    listing = folder / "partial_movie_file_list.txt"
    # The log parser reads only MP4 segment lines, so the WebM segment is kept for its complete container.
    fake_manim.plan(
        {finished: complete_clip(suffix), cut_off: _cut_off_clip(suffix), listing: b""},
        [f"Animation 0 : Partial movie file written in '{finished}'", "RuntimeError: killed"],
        exit_code=1,
    )
    _run(supervisor, command, "job-1")
    assert _terminal(events, "job-1")["event"] == "error"
    assert fake_manim.seen()["marker"] is True
    assert finished.is_file()
    assert not cut_off.exists()
    assert not listing.exists()
    assert not supervisor.run_marker.exists()


# PNG sequence (F021) and the 480p15 guard (G4.9)


def test_png_export_publishes_only_the_frames_of_this_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    # A folder named 480p15 above the project must not look like the preview.
    project = _project(tmp_path / "480p15" / "lesson", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = png_frames_dir(supervisor.media_dir, "scene")
    frames.mkdir(parents=True)
    for index in range(6):
        (frames / f"{index:04d}.png").write_bytes(b"stale")
    fake_manim.plan(_png_render(frames, 3), _wrapped_images_ready(3, frames))
    _run(supervisor, "export", "export-1")
    done = _terminal(events, "export-1")
    assert done["event"] == "done", done
    seen = fake_manim.seen()["files"]
    assert not [name for name in seen if name.startswith("images/")]
    assert supervisor.session is not None
    captured = supervisor.session.project
    published = (
        project / ".mathimator" / "exports" / export_folder_name(captured) / export_artifact_name("png_sequence")
    )
    assert done["path"] == str(published)
    assert sorted(path.name for path in published.iterdir()) == ["0000.png", "0001.png", "0002.png"]


def test_png_export_with_a_frame_count_mismatch_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "mismatch", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = png_frames_dir(supervisor.media_dir, "scene")
    fake_manim.plan(_png_render(frames, 3), _wrapped_images_ready(4, frames))
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["event"] == "error"
    assert "reported 4 PNG frames" in str(failed["message"])
    assert not (project / ".mathimator" / "exports").exists()


def test_png_export_without_a_frame_count_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "uncounted", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = png_frames_dir(supervisor.media_dir, "scene")
    fake_manim.plan(_png_render(frames, 3))
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["message"] == f"manim did not report how many PNG frames it wrote to {frames}"
    assert not (project / ".mathimator" / "exports").exists()


def test_png_export_stops_when_the_old_frames_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "in-use", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = png_frames_dir(supervisor.media_dir, "scene")
    frames.mkdir(parents=True)
    stale = frames / "0000.png"
    stale.write_bytes(b"stale")

    def in_use(path: Path) -> None:
        if path == frames:
            raise PermissionError(13, "The process cannot access the file", str(path))
        _remove_tree(path)

    monkeypatch.setattr("mathimator_worker.supervisor._remove_tree", in_use)
    fake_manim.plan(_png_render(frames, 3), _wrapped_images_ready(3, frames))
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["event"] == "error"
    assert str(failed["message"]).startswith(f"could not remove the old PNG frames in {frames}: ")
    assert not fake_manim.ran()
    assert not supervisor.run_marker.exists()
    assert stale.read_bytes() == b"stale"
    assert not (project / ".mathimator" / "exports").exists()


def test_mp4_export_publishes_from_a_project_under_a_480p15_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "480p15" / "lesson")
    supervisor, events = _supervisor(project, monkeypatch)
    fake_manim.plan(video_render(supervisor.media_dir, "1080p30", [complete_clip(".mp4")] * 4))
    _run(supervisor, "export", "export-1")
    done = _terminal(events, "export-1")
    assert done["event"] == "done", done
    assert supervisor.session is not None
    folder = project / ".mathimator" / "exports" / export_folder_name(supervisor.session.project)
    assert done["path"] == str(folder / export_artifact_name("mp4"))
    # The preview movie is still refused for a 1080p30 export.
    preview = supervisor.media_dir / "videos" / "scene" / "480p15" / "MainScene.mp4"
    preview.parent.mkdir(parents=True)
    write_clip(preview)
    job = _job("export")
    job.project_dir = supervisor.project
    job.media_dir = supervisor.media_dir
    job.quality = "1080p30"
    job.captured = supervisor.session.project
    with supervisor._doc_lock:
        assert supervisor._publish_export_locked(job, preview) is None


def _cut_off_clip(suffix: str) -> bytes:
    """What FFmpeg leaves when it is killed before it closes the file."""
    if suffix == ".webm":
        # The Segment still has the "unknown" size it is written with.
        header = ebml_element(0x1A45DFA3, ebml_element(0x4282, b"webm"))
        cluster = ebml_element(0x1F43B675, bytes(32))
        return header + bytes.fromhex("18538067 01ffffffffffffff") + cluster
    # mdat still has size 0 (to the end of the file), and moov was never written.
    return iso_box(b"ftyp", b"isom" + bytes(4)) + iso_box(b"free") + struct.pack(">I4s", 0, b"mdat") + bytes(64)


def _project(root: Path, export: dict[str, object] | None = None) -> Path:
    session = Session(load_text(JOURNEY.read_text(encoding="utf-8")))
    if export is not None:
        session.edit({"op": "set_export_profile", **export})
    root.mkdir(parents=True)
    (root / "project.json").write_text(dumps_project(session.project), encoding="utf-8", newline="\n")
    return root


def _supervisor(project: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Supervisor, list[dict[str, object]]]:
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    supervisor = Supervisor(project, sys.executable, project.parent / f"{project.name}.log", True)
    events: list[dict[str, object]] = []
    monkeypatch.setattr(supervisor, "emit", events.append)
    return supervisor, events


def _job(profile: str) -> _Job:
    return _Job(
        job_id=f"{profile}-1",
        profile=profile,
        thread=threading.Thread(target=lambda: None),
        cancel=threading.Event(),
    )


def _run(supervisor: Supervisor, command: str, job_id: str) -> None:
    supervisor.handle_line(json.dumps({"cmd": command, "id": job_id}))
    job = supervisor._current
    if job is not None:
        job.thread.join(timeout=60)
        assert not job.thread.is_alive()


def _terminal(events: list[dict[str, object]], job_id: str) -> dict[str, object]:
    terminal = {"done", "error", "rejected"}
    ends = [event for event in events if event.get("jobId") == job_id and event.get("event") in terminal]
    assert len(ends) == 1, events
    return ends[0]


def _png_render(frames: Path, count: int) -> dict[Path, bytes]:
    return {frames / f"{index:04d}.png": b"png" for index in range(count)}


def _wrapped_images_ready(count: int, frames: Path) -> list[str]:
    """Manim's closing PNG line as Rich wraps it into a narrow column."""
    pad = " " * 29
    text = str(frames)
    folder = [pad + text[start : start + 26] for start in range(0, len(text), 26)]
    return [
        "[10/22/26 22:27:38] INFO" + " " * 32 + "scene_file_writer.py:561",
        f"{pad}{count} images ready at",
        *folder,
    ]


def _write_marker(project: Path, text: str) -> Path:
    marker = project / ".mathimator" / "cache" / "manim-run.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(text, encoding="utf-8")
    return marker


def _interrupted_media(media: Path, started: float) -> tuple[Path, list[Path]]:
    """An older finished partial, plus what a run that started at ``started`` left."""
    folder = partial_movie_dir(media, "scene", "1080p30")
    folder.mkdir(parents=True)
    kept = _aged(write_clip(folder / ("a" * 64 + ".mp4")), started - 60)
    cut_off = folder / ("b" * 64 + ".mp4")
    cut_off.write_bytes(_cut_off_clip(".mp4"))
    listing = folder / "partial_movie_file_list.txt"
    listing.write_text(f"file 'file:{cut_off.as_posix()}'\n", encoding="utf-8")
    movie = write_clip(folder.parent.parent / "MainScene.mp4")
    return kept, [_aged(path, started + 5) for path in (cut_off, listing, movie)]


def _aged(path: Path, when: float) -> Path:
    os.utime(path, (when, when))
    return path


def _import_file(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("planted_scene_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _short_path(path: Path) -> Path:
    """The 8.3 alias Windows gives ``path``, or ``path`` itself when the volume has none."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_short_path = kernel32.GetShortPathNameW
    get_short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_short_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_short_path(str(path), buffer, len(buffer))
    return Path(buffer.value) if 0 < length < len(buffer) else path
