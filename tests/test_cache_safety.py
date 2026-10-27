"""Scene code and cache safety.

The generated-code folder, the startup project path, PNG-sequence frames, the
interrupted-run marker, and the completeness check for partial movies. No test
here starts Manim. Jobs run the ``fake_manim`` stand-in (``support.FakeManim``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import stat
import struct
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from mathimator_worker import clip_check
from mathimator_worker.clip_check import ClipChecker, clip_is_complete
from mathimator_worker.codegen import generate
from mathimator_worker.document import dumps_project, load_text
from mathimator_worker.exports import _export_problem
from mathimator_worker.identity import beat_chain_keys, export_folder_name, export_key
from mathimator_worker.layout import ProjectLayout
from mathimator_worker.manim_paths import short_path
from mathimator_worker.progress import MANIFEST
from mathimator_worker.project_folder import retry_read_only
from mathimator_worker.session import Session
from mathimator_worker.supervisor import Supervisor, _Job, _remove_tree
from tests.support import (
    REPO,
    TERMINAL_EVENTS,
    FakeManim,
    complete_clip,
    ebml_element,
    iso_box,
    link_folder,
    video_render,
    write_clip,
)

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"
SUFFIXES = (".mp4", ".mov", ".webm")
_PNG_PROFILE = {"format": "png_sequence", "width": 1280, "height": 720, "fps": 30, "transparent": False}
# Journey A's 8 s at the 30 fps of _PNG_PROFILE: one PNG per frame.
_PNG_FRAMES = 240
_WEBM_PROFILE = {"format": "webm", "width": 1920, "height": 1080, "fps": 30, "transparent": False}

# Completeness check


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


def test_an_incomplete_partial_never_makes_its_beat_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path / "index")
    session = Session(load_text((project / "project.json").read_text(encoding="utf-8")))
    session.mark_rendered("preview")
    cache = project / ".mathimator" / "cache"
    folder = ProjectLayout(project).partial_movie_dir("480p15")
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
    for beat, key in zip(beats, beat_chain_keys(session.project, "preview"), strict=False):
        beat["partialKey"] = key
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
    folder = supervisor.layout.partial_movie_dir("480p15")
    folder.mkdir(parents=True)
    kept = _aged(write_clip(folder / ("c" * 64 + ".mp4")), time.time() - 600)
    cut_off = folder / ("d" * 64 + ".mp4")
    cut_off.write_bytes(_cut_off_clip(".mp4"))
    _aged(cut_off, time.time() - 600)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, "render_preview", "preview-1")
    assert _terminal(events, "preview-1")["event"] == "done"
    seen = fake_manim.seen()["files"]
    assert kept.relative_to(supervisor.layout.media_dir).as_posix() in seen
    assert cut_off.relative_to(supervisor.layout.media_dir).as_posix() not in seen
    assert kept.is_file()
    assert not cut_off.exists()


def test_an_export_that_used_an_incomplete_clip_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "webm", export=_WEBM_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    clips = [complete_clip(".webm"), _cut_off_clip(".webm"), complete_clip(".webm"), complete_clip(".webm")]
    files = video_render(supervisor.layout, "1080p30", clips, suffix=".webm")
    fake_manim.plan(files)
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["event"] == "error"
    assert "incomplete clip" in str(failed["message"])
    partials = [path for path in files if path.suffix == ".webm" and "partial_movie_files" in path.parts]
    assert [path.exists() for path in partials] == [True, False, True, True]
    assert not (project / ".mathimator" / "exports").exists()


def test_png_export_ignores_the_segment_list_of_another_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "frames", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    folder = supervisor.layout.partial_movie_dir("480p15")
    folder.mkdir(parents=True)
    cut_off = folder / ("a" * 64 + ".mp4")
    cut_off.write_bytes(_cut_off_clip(".mp4"))
    (folder / "partial_movie_file_list.txt").write_text(f"file 'file:{cut_off.as_posix()}'\n", encoding="utf-8")
    frames = supervisor.layout.png_frames_dir
    fake_manim.plan(_png_render(frames, _PNG_FRAMES), _wrapped_images_ready(_PNG_FRAMES, frames))
    _run(supervisor, "export", "export-1")
    assert _terminal(events, "export-1")["event"] == "done"
    assert not [event for event in events if event["event"] == "beat_ready"]
    # The next preview of that quality removes it before Manim starts.
    assert cut_off.is_file()


# Codegen folder


def test_a_render_replaces_a_planted_codegen_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, _events = _supervisor(_project(tmp_path / "shared"), monkeypatch)
    scene = supervisor.layout.scene_path
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


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to delete a read-only file")
def test_a_render_replaces_a_read_only_codegen_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    supervisor, events = _supervisor(_project(tmp_path / "read-only"), monkeypatch)
    scene = supervisor.layout.scene_path
    bytecode = Path(importlib.util.cache_from_source(str(scene)))
    bytecode.parent.mkdir(parents=True)
    bytecode.write_bytes(b"planted")
    scene.write_text("REAL = True\n", encoding="utf-8")
    # A copied or shared project folder can carry the read-only attribute.
    for path in (bytecode, bytecode.parent, scene):
        path.chmod(stat.S_IREAD)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, "render_preview", "preview-1")
    assert _terminal(events, "preview-1")["event"] == "done"
    assert not bytecode.parent.exists()
    assert supervisor.session is not None
    assert scene.read_text(encoding="utf-8") == generate(supervisor.session.project)


# Read-only files. The tree removals call the handler only on Windows,
# so these call it directly.


@pytest.mark.parametrize("function", [os.unlink, os.rmdir], ids=["file", "folder"])
def test_a_read_only_file_or_folder_is_made_writable_and_removed(
    tmp_path: Path, function: Callable[[str], object]
) -> None:
    path = _read_only(tmp_path / "read-only", folder=function is os.rmdir)
    retry_read_only(function, str(path), PermissionError(13, "Access is denied", str(path)))
    assert not os.path.lexists(path)


@pytest.mark.parametrize(
    ("function", "failure", "writable"),
    [
        pytest.param(os.unlink, PermissionError(13, "The file is in use"), True, id="writable-file"),
        pytest.param(os.unlink, OSError(16, "Device or resource busy"), False, id="not-a-refusal"),
        pytest.param(os.scandir, PermissionError(13, "Access is denied"), False, id="listing"),
        pytest.param(os.open, PermissionError(13, "Access is denied"), False, id="opening"),
    ],
)
def test_the_read_only_handler_raises_any_other_failure_again(
    tmp_path: Path, function: Callable[[str], object], failure: OSError, writable: bool
) -> None:
    # rmtree reports a folder it could not list or open with the call that failed.
    path = _read_only(tmp_path / "kept", folder=function is not os.unlink)
    if writable:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
    with pytest.raises(OSError) as raised:
        retry_read_only(function, str(path), failure)
    assert raised.value is failure
    assert bool(path.stat().st_mode & stat.S_IWRITE) is writable


def test_the_read_only_handler_never_changes_a_link_or_its_target(tmp_path: Path) -> None:
    target = _read_only(tmp_path / "outside", folder=True)
    link = tmp_path / "linked"
    link_folder(link, target)
    if os.chmod in os.supports_follow_symlinks:
        # Windows refuses to delete a read-only junction, as it does a read-only folder.
        os.chmod(link, stat.S_IREAD | stat.S_IEXEC, follow_symlinks=False)
    refused = PermissionError(13, "Access is denied", str(link))
    with pytest.raises(PermissionError) as raised:
        retry_read_only(os.rmdir, str(link), refused)
    assert raised.value is refused
    assert os.path.lexists(link)
    assert not target.stat().st_mode & stat.S_IWRITE


# Startup path


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 aliases exist only on Windows")
def test_startup_resolves_a_short_path_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "a project folder with a long name"
    folder.mkdir()
    alias = short_path(folder)
    if "~" not in alias.name:
        pytest.skip("this volume does not create 8.3 names")
    supervisor, events = _supervisor(alias, monkeypatch)
    assert supervisor.project == folder
    supervisor.handle_line(json.dumps({"cmd": "get_state", "id": "state"}))
    assert events[-1]["projectPath"] == str(folder)
    job = _job("preview")
    with supervisor._doc_lock:
        assert supervisor._prepare_job_locked(job)
    # Manim runs in the long form: the path has no characters that need the alias.
    assert job.manim_dir == folder / ".mathimator" / "codegen"


# Interrupted-run marker


def test_startup_sweeps_media_left_by_an_interrupted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "crashed")
    supervisor, _events = _supervisor(project, monkeypatch)
    folder = supervisor.layout.partial_movie_dir("1080p30")
    folder.mkdir(parents=True)
    earlier = [write_clip(folder / ("a" * 64 + ".mp4")), write_clip(folder.parent.parent / "MainScene.mp4")]
    # Another quality folder, which the export does not write into.
    other = supervisor.layout.partial_movie_dir("480p15")
    other.mkdir(parents=True)
    elsewhere = [_written(other / ("e" * 64 + ".mp4"), _cut_off_clip(".mp4")), _written(other / MANIFEST, b"")]
    finished, cut_off, listing = folder / ("b" * 64 + ".mp4"), folder / ("c" * 64 + ".mp4"), folder / MANIFEST
    run = {finished: complete_clip(".mp4"), cut_off: _cut_off_clip(".mp4"), listing: b""}
    _crash(supervisor, monkeypatch, fake_manim, run)
    # A file server whose clock runs an hour behind stamped the run's partial in the past.
    _aged(cut_off, time.time() - 3600)
    restarted, _events = _supervisor(project, monkeypatch)
    assert (cut_off.exists(), listing.exists()) == (False, False)
    # A complete partial of the run stays, so the next render reuses it.
    assert all(path.is_file() for path in [finished, *earlier, *elsewhere])
    assert not restarted.layout.run_marker.exists()
    assert f"removed unfinished media {cut_off}\n" in restarted.log_path.read_text(encoding="utf-8")


def test_startup_sweeps_the_frames_of_an_interrupted_png_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "frames", export=_PNG_PROFILE)
    supervisor, _events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    _crash(supervisor, monkeypatch, fake_manim, _png_render(frames, 30))
    assert frames.is_dir()
    _supervisor(project, monkeypatch)
    assert not frames.exists()
    assert not supervisor.layout.run_marker.exists()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param('{"quality": ', id="truncated"),
        pytest.param('{"started": 1792800000.0}\n', id="older-build"),
        pytest.param('{"quality": "../../../../../../outside", "format": "mp4", "files": {}}', id="outside"),
        pytest.param('{"quality": "480p15", "format": "mp4", "files": {"MainScene.mp4": [1]}}', id="bad-stamp"),
    ],
)
def test_a_marker_that_cannot_be_read_sweeps_every_quality_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    project = _project(tmp_path / "garbled")
    layout = ProjectLayout(project)
    outside = _foreign_quality_folder(tmp_path / "outside")
    kept: list[Path] = []
    swept: list[Path] = []
    for quality in ("480p15", "1080p30"):
        folder = layout.partial_movie_dir(quality)
        folder.mkdir(parents=True)
        kept.append(write_clip(folder / ("a" * 64 + ".mp4")))
        swept.append(_written(folder / ("b" * 64 + ".mp4"), _cut_off_clip(".mp4")))
        swept += [_written(folder / MANIFEST, b""), write_clip(folder.parent.parent / "MainScene.mp4")]
    link_folder(layout.video_dir("720p30"), tmp_path / "outside")
    frames = layout.png_frames_dir
    frames.mkdir(parents=True)
    swept.append(_written(frames / "0000.png", b"png"))
    marker = _write_marker(project, text)
    _supervisor(project, monkeypatch)
    # Complete partials stay, and nothing outside the project is touched, through a link or not.
    assert all(path.is_file() for path in [*kept, *outside])
    assert [path for path in swept if path.exists()] == []
    assert not frames.exists()
    assert not marker.exists()


@pytest.mark.parametrize(
    "linked",
    [pytest.param("", id="quality-folder"), pytest.param("partial_movie_files", id="partial-movie-files")],
)
def test_the_sweep_never_deletes_through_a_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: str) -> None:
    """A link planted in the quality folder the marker names never lets a bind delete outside the project."""
    project = _project(tmp_path / "linked")
    outside = _foreign_quality_folder(tmp_path / "outside")
    link = ProjectLayout(project).video_dir("1080p30") / linked
    link.parent.mkdir(parents=True)
    link_folder(link, tmp_path / "outside" / linked)
    marker = _write_marker(project, json.dumps({"quality": "1080p30", "format": "mp4", "files": {}}))
    _supervisor(project, monkeypatch)
    assert all(path.is_file() for path in outside)
    assert not marker.exists()


def test_a_handwritten_startup_never_sweeps_through_a_linked_media_folder(tmp_path: Path, link_kind: str) -> None:
    """Hand-written mode binds without the link check, so each delete of its sweep checks for links."""
    elsewhere = tmp_path / "elsewhere"
    outside = _foreign_quality_folder(elsewhere / "videos" / "scene" / "1080p30")
    (elsewhere / "images" / "scene" / "MainScene").mkdir(parents=True)
    outside.append(_written(elsewhere / "images" / "scene" / "MainScene" / "0000.png", b"png"))
    folder = tmp_path / "handwritten"
    media = ProjectLayout(folder).media_dir
    media.parent.mkdir(parents=True)
    link_folder(media, elsewhere, link_kind)
    # A marker that cannot be read sweeps every quality folder and the PNG folder.
    marker = _write_marker(folder, '{"quality": ')
    log = tmp_path / "handwritten.log"
    Supervisor(folder, sys.executable, log, False).close()
    assert all(path.is_file() for path in outside)
    # The PNG folder is still there, so the next bind sweeps again.
    assert marker.is_file()
    assert f"{media} is a link to another folder" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("linked", ["codegen", "cache/media/images"])
def test_a_render_after_a_folder_became_a_link_is_refused_and_deletes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim, linked: str, link_kind: str
) -> None:
    supervisor, events = _supervisor(_project(tmp_path / "shared", export=_PNG_PROFILE), monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "scene" / "MainScene").mkdir(parents=True)
    mine = [
        _written(elsewhere / "scene.py", b"mine"),
        _written(elsewhere / "scene" / "MainScene" / "0000.png", b"mine"),
    ]
    # Where the codegen reset and the PNG export delete before Manim starts.
    link = supervisor.project / ".mathimator" / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link_folder(link, elsewhere, link_kind)
    _run(supervisor, "export", "export-1")
    refused = _terminal(events, "export-1")
    assert (refused["event"], refused["code"]) == ("rejected", "linked_folder")
    assert str(refused["message"]).startswith(f"Mathimator cannot use {link}: ")
    assert not fake_manim.ran()
    assert [path.read_bytes() for path in mine] == [b"mine", b"mine"]


@pytest.mark.parametrize(("command", "quality"), [("render_preview", "480p15"), ("export", "1080p30")])
@pytest.mark.parametrize(
    "linked",
    [pytest.param("", id="quality-folder"), pytest.param("partial_movie_files", id="partial-movie-files")],
)
def test_a_render_into_a_linked_quality_folder_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_manim: FakeManim,
    command: str,
    quality: str,
    linked: str,
    link_kind: str,
) -> None:
    """Manim would write, and prune its partial movies, through a link in the render's quality folder."""
    supervisor, events = _supervisor(_project(tmp_path / "shared"), monkeypatch)
    outside = tmp_path / "outside"
    _foreign_quality_folder(outside)
    before = _tree(outside)
    link = supervisor.layout.video_dir(quality) / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link_folder(link, outside / linked, link_kind)
    _run(supervisor, command, "job-1")
    refused = _terminal(events, "job-1")
    assert (refused["event"], refused["code"]) == ("rejected", "linked_folder")
    assert str(refused["message"]).startswith(f"Mathimator cannot use {link}: ")
    assert not fake_manim.ran()
    assert _tree(outside) == before


@pytest.mark.parametrize("export", [pytest.param(None, id="mp4"), pytest.param(_PNG_PROFILE, id="png-sequence")])
def test_an_export_into_a_linked_export_folder_is_refused_and_changes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_manim: FakeManim,
    export: dict[str, object] | None,
    link_kind: str,
) -> None:
    """A link at exports/<key16> would publish an export, or find a finished one, outside the project."""
    supervisor, events = _supervisor(_project(tmp_path / "shared", export=export), monkeypatch)
    assert supervisor.session is not None
    project = supervisor.session.project
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "frames").mkdir(parents=True)
    _written(elsewhere / "frames" / "precious.png", b"mine")
    _written(elsewhere / "scene.mp4", b"mine")
    # With the key of this export, the export.json there would make it look finished already.
    _written(elsewhere / "export.json", json.dumps({"exportKey": export_key(project)}).encode("utf-8"))
    before = _tree(elsewhere)
    link = supervisor.layout.export_dir(project)
    link.parent.mkdir(parents=True)
    link_folder(link, elsewhere, link_kind)
    _run(supervisor, "export", "export-1")
    refused = _terminal(events, "export-1")
    assert (refused["event"], refused["code"]) == ("rejected", "linked_folder")
    assert str(refused["message"]).startswith(f"Mathimator cannot use {link}: ")
    assert not fake_manim.ran()
    assert _tree(elsewhere) == before


def test_a_sweep_that_cannot_remove_a_file_keeps_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "held")
    supervisor, _events = _supervisor(project, monkeypatch)
    cut_off = supervisor.layout.partial_movie_dir("1080p30") / ("c" * 64 + ".mp4")
    _crash(supervisor, monkeypatch, fake_manim, {cut_off: _cut_off_clip(".mp4")})

    def in_use(path: Path) -> None:
        raise PermissionError(13, "The process cannot access the file", str(path))

    with monkeypatch.context() as patch:
        patch.setattr("mathimator_worker.progress.remove_file", in_use)
        held, _events = _supervisor(project, monkeypatch)
    assert cut_off.is_file()
    assert held.layout.run_marker.is_file()
    assert f"could not remove unfinished media {cut_off}: " in held.log_path.read_text(encoding="utf-8")
    # The next bind sweeps again.
    held.close()
    _supervisor(project, monkeypatch)
    assert not cut_off.exists()
    assert not held.layout.run_marker.exists()


def test_open_project_sweeps_an_interrupted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "second")
    crashed, _events = _supervisor(project, monkeypatch)
    cut_off = crashed.layout.partial_movie_dir("1080p30") / ("c" * 64 + ".mp4")
    _crash(crashed, monkeypatch, fake_manim, {cut_off: _cut_off_clip(".mp4")})
    supervisor, events = _supervisor(tmp_path / "first", monkeypatch)
    supervisor.handle_line(json.dumps({"cmd": "open_project", "id": "open", "path": str(project)}))
    assert events[-1]["event"] == "state"
    assert not cut_off.exists()
    assert not supervisor.layout.run_marker.exists()


def test_open_project_leaves_a_running_export_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "busy")
    supervisor, events = _supervisor(project, monkeypatch)
    files = video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4)
    fake_manim.plan(files, hold=True)
    supervisor.handle_line(json.dumps({"cmd": "export", "id": "export-1"}))
    job = supervisor._current
    assert job is not None
    try:
        fake_manim.wait_until_held()
        supervisor.handle_line(json.dumps({"cmd": "open_project", "id": "open", "path": str(project)}))
        # The open is refused before it can sweep the export's media.
        assert (events[-1]["event"], events[-1]["code"]) == ("rejected", "export_running")
        assert supervisor.layout.run_marker.is_file()
        assert all(path.is_file() for path in files)
    finally:
        fake_manim.release()
        job.thread.join(timeout=60)
    assert not job.thread.is_alive()
    assert _terminal(events, "export-1")["event"] == "done"
    assert not supervisor.layout.run_marker.exists()


def test_reopening_a_folder_leaves_a_preview_that_outlived_its_stop_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "busy")
    supervisor, events = _supervisor(project, monkeypatch)
    # A Manim that is still alive when the 1 s the switch waits for it is over.
    monkeypatch.setattr(supervisor, "_kill_process", lambda job: None)
    files = video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)
    fake_manim.plan(files, hold=True)
    supervisor.handle_line(json.dumps({"cmd": "render_preview", "id": "preview-1"}))
    job = supervisor._current
    assert job is not None
    try:
        fake_manim.wait_until_held()
        supervisor.handle_line(json.dumps({"cmd": "open_project", "id": "open", "path": str(project)}))
        # The bind does not sweep the media of a run that is still writing it.
        assert [event["event"] for event in events if event.get("jobId") == "open"] == ["state"]
        assert supervisor.layout.run_marker.is_file()
        assert all(path.is_file() for path in files)
    finally:
        fake_manim.release()
        job.thread.join(timeout=60)
    assert not job.thread.is_alive()
    assert _terminal(events, "preview-1")["code"] == "cancelled"


def test_the_run_marker_records_the_quality_folder_while_manim_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    supervisor, events = _supervisor(_project(tmp_path / "preview"), monkeypatch)
    folder = supervisor.layout.partial_movie_dir("480p15")
    folder.mkdir(parents=True)
    earlier = write_clip(folder / ("c" * 64 + ".mp4"))
    other = supervisor.layout.partial_movie_dir("1080p30")
    other.mkdir(parents=True)
    write_clip(other / ("d" * 64 + ".mp4"))
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, "render_preview", "preview-1")
    assert _terminal(events, "preview-1")["event"] == "done"
    info = earlier.stat()
    # Paths relative to the quality folder, so a moved project still reads them.
    assert fake_manim.seen()["marker"] == {
        "quality": "480p15",
        "format": "mp4",
        "files": {f"partial_movie_files/MainScene/{earlier.name}": [info.st_size, info.st_mtime_ns]},
    }
    assert not supervisor.layout.run_marker.exists()
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
    folder = supervisor.layout.partial_movie_dir(quality)
    finished = folder / ("e" * 64 + suffix)
    cut_off = folder / ("f" * 64 + suffix)
    listing = folder / "partial_movie_file_list.txt"
    # The finished partial stays for the next render, whatever its container.
    fake_manim.plan(
        {finished: complete_clip(suffix), cut_off: _cut_off_clip(suffix), listing: b""},
        ["RuntimeError: killed"],
        exit_code=1,
    )
    _run(supervisor, command, "job-1")
    assert _terminal(events, "job-1")["event"] == "error"
    assert fake_manim.seen()["marker"] is not None
    assert finished.is_file()
    assert not cut_off.exists()
    assert not listing.exists()
    assert not supervisor.layout.run_marker.exists()


# PNG sequence and the 480p15 guard


def test_png_export_publishes_only_the_frames_of_this_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    # A folder named 480p15 above the project must not look like the preview.
    project = _project(tmp_path / "480p15" / "lesson", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    frames.mkdir(parents=True)
    for index in range(_PNG_FRAMES + 3):
        (frames / f"{index:04d}.png").write_bytes(b"stale")
    fake_manim.plan(_png_render(frames, _PNG_FRAMES), _wrapped_images_ready(_PNG_FRAMES, frames))
    _run(supervisor, "export", "export-1")
    done = _terminal(events, "export-1")
    assert done["event"] == "done", done
    seen = fake_manim.seen()["files"]
    assert not [name for name in seen if name.startswith("images/")]
    assert supervisor.session is not None
    captured = supervisor.session.project
    published = project / ".mathimator" / "exports" / export_folder_name(captured) / "frames"
    assert done["path"] == str(published)
    assert sorted(path.name for path in published.iterdir()) == [f"{index:04d}.png" for index in range(_PNG_FRAMES)]


def test_png_export_with_a_frame_count_mismatch_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "mismatch", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    fake_manim.plan(_png_render(frames, _PNG_FRAMES), _wrapped_images_ready(_PNG_FRAMES + 1, frames))
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["event"] == "error"
    assert failed["message"] == f"manim reported {_PNG_FRAMES + 1} PNG frames, but {frames} holds {_PNG_FRAMES}"
    assert not (project / ".mathimator" / "exports").exists()
    assert not frames.exists()


def test_png_export_short_of_the_documents_frames_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    # Manim 0.22 saved the 30 frames of Journey A's 1 s wait as one PNG, and counted that one.
    project = _project(tmp_path / "short", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    fake_manim.plan(_png_render(frames, _PNG_FRAMES - 29), _wrapped_images_ready(_PNG_FRAMES - 29, frames))
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert (failed["event"], failed["code"]) == ("error", "render_failed")
    assert failed["message"] == f"the scene has 240 frames, but manim wrote 211 PNG frames to {frames}"
    assert not (project / ".mathimator" / "exports").exists()
    assert not frames.exists()


def test_png_export_without_a_frame_count_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "uncounted", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    fake_manim.plan(_png_render(frames, _PNG_FRAMES))
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["message"] == f"manim did not report how many PNG frames it wrote to {frames}"
    assert not (project / ".mathimator" / "exports").exists()
    assert not frames.exists()


def test_a_failed_png_export_leaves_no_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "failed", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    fake_manim.plan(_png_render(frames, 30), ["RuntimeError: killed"], exit_code=1)
    _run(supervisor, "export", "export-1")
    assert _terminal(events, "export-1")["event"] == "error"
    assert fake_manim.seen()["marker"]["format"] == "png_sequence"
    assert not frames.exists()
    assert f"removed unfinished media {frames}\n" in supervisor.log_path.read_text(encoding="utf-8")


def test_a_cancelled_png_export_leaves_no_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "cancelled", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    fake_manim.plan(_png_render(frames, 30), hold=True)
    supervisor.handle_line(json.dumps({"cmd": "export", "id": "export-1"}))
    job = supervisor._current
    assert job is not None
    try:
        fake_manim.wait_until_held()
        assert frames.is_dir()
        supervisor.handle_line(json.dumps({"cmd": "cancel", "id": "cancel-1", "jobId": "export-1"}))
        job.thread.join(timeout=60)
    finally:
        fake_manim.release()
    assert not job.thread.is_alive()
    assert _terminal(events, "export-1")["code"] == "cancelled"
    assert not frames.exists()
    assert not supervisor.layout.run_marker.exists()


def test_png_export_stops_when_the_old_frames_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "in-use", export=_PNG_PROFILE)
    supervisor, events = _supervisor(project, monkeypatch)
    frames = supervisor.layout.png_frames_dir
    frames.mkdir(parents=True)
    stale = frames / "0000.png"
    stale.write_bytes(b"stale")

    def in_use(root: Path, path: Path) -> None:
        if path == frames:
            raise PermissionError(13, "The process cannot access the file", str(path))
        _remove_tree(root, path)

    monkeypatch.setattr("mathimator_worker.supervisor._remove_tree", in_use)
    fake_manim.plan(_png_render(frames, 3), _wrapped_images_ready(3, frames))
    _run(supervisor, "export", "export-1")
    failed = _terminal(events, "export-1")
    assert failed["event"] == "error"
    assert str(failed["message"]).startswith(f"could not remove the old PNG frames in {frames}: ")
    assert not fake_manim.ran()
    assert not supervisor.layout.run_marker.exists()
    assert stale.read_bytes() == b"stale"
    assert not (project / ".mathimator" / "exports").exists()


def test_mp4_export_publishes_from_a_project_under_a_480p15_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim
) -> None:
    project = _project(tmp_path / "480p15" / "lesson")
    supervisor, events = _supervisor(project, monkeypatch)
    fake_manim.plan(video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4))
    _run(supervisor, "export", "export-1")
    done = _terminal(events, "export-1")
    assert done["event"] == "done", done
    assert supervisor.session is not None
    folder = project / ".mathimator" / "exports" / export_folder_name(supervisor.session.project)
    assert done["path"] == str(folder / "scene.mp4")
    # The preview movie is still refused for a 1080p30 export.
    preview = supervisor.layout.media_dir / "videos" / "scene" / "480p15" / "MainScene.mp4"
    preview.parent.mkdir(parents=True)
    write_clip(preview)
    problem = _export_problem(preview, Path(done["path"]), "mp4", supervisor.layout.media_dir, "1080p30")
    assert problem == "the render is the preview"


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
    ends = [event for event in events if event.get("jobId") == job_id and event.get("event") in TERMINAL_EVENTS]
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


def _crash(
    supervisor: Supervisor, monkeypatch: pytest.MonkeyPatch, fake_manim: FakeManim, files: dict[Path, bytes]
) -> None:
    """Export with a Manim that writes ``files``, then leave them and the run marker as a worker that died would."""
    monkeypatch.setattr(supervisor, "_discard", lambda job, progress: None)
    monkeypatch.setattr(supervisor, "_remove_run_marker", lambda marker: None)
    fake_manim.plan(files, ["Segmentation fault"], exit_code=1)
    _run(supervisor, "export", "export-1")
    assert supervisor.layout.run_marker.is_file()
    supervisor.close()


def _written(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _tree(folder: Path) -> dict[str, bytes | None]:
    """Every file below ``folder`` with its bytes, and every folder with None."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes() if path.is_file() else None for path in folder.rglob("*")
    }


def _read_only(path: Path, *, folder: bool) -> Path:
    """A read-only empty folder or file at ``path``, as a copied or shared project folder can carry one."""
    if folder:
        path.mkdir()
    else:
        path.write_bytes(b"old")
    path.chmod(stat.S_IREAD | stat.S_IEXEC)
    return path


def _foreign_quality_folder(folder: Path) -> list[Path]:
    """A quality folder outside the project, with the files a sweep takes: a movie, a segment list, a cut-off partial."""
    partials = folder / "partial_movie_files" / "MainScene"
    partials.mkdir(parents=True)
    return [
        write_clip(folder / "MainScene.mp4"),
        _written(partials / MANIFEST, b""),
        _written(partials / ("b" * 64 + ".mp4"), _cut_off_clip(".mp4")),
    ]


def _aged(path: Path, when: float) -> Path:
    os.utime(path, (when, when))
    return path


def _import_file(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("planted_scene_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
