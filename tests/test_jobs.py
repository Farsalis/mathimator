"""Jobs, progress and publishing (contract C7.1, C7.2; fix-plan S28, S35 to S38).

The job queue with its hold slots, progress from the files Manim writes,
``beat_ready`` under the document lock, the tagged preview index, publishing an
export, and the ``render_failed`` messages. Supervisors run in this process
through ``handle_line``, and jobs run the ``fake_manim`` stand-in, so no test
starts Manim.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import NoReturn

import pytest

from mathimator_worker import exports as exports_module
from mathimator_worker import progress as progress_module
from mathimator_worker import project_folder
from mathimator_worker import supervisor as supervisor_module
from mathimator_worker.clip_check import ClipChecker
from mathimator_worker.document import empty_project
from mathimator_worker.preview_index import PreviewIndex
from mathimator_worker.progress import MANIFEST, ManimProgress
from mathimator_worker.project_folder import copy_atomically, remove_file
from mathimator_worker.session import Session
from mathimator_worker.supervisor import Supervisor, _Job
from mathimator_worker.tex_failure import format_render_failure, tex_error_lines
from tests.support import REPO, FakeManim, Stdout, complete_clip, iso_box, video_render, write_clip

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"
CAPTURES = REPO / "tests" / "fixtures" / "manim"
# The message for the TeX log in CAPTURES of a formula that misspells \sin (D11).
UNDEFINED_SINN = "LaTeX failed.\n! Undefined control sequence.\n<argument>  y = \\sinn\nl.8 \\end{align*}"
QUEUED = {"event": "queued", "code": "stopping_previous", "message": "Waiting for the previous render to stop."}
CANCELLED = {"event": "error", "code": "cancelled", "message": "cancelled"}
IN_USE = "The process cannot access the file because it is being used by another process"


@pytest.fixture
def quick_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill the fake Manim directly, so a cancel without a Job Object needs no process table helper."""
    monkeypatch.setattr(supervisor_module, "terminate_process_group", lambda proc: proc.kill())


# The queue (C7.2, D19, F036)


def test_a_preview_that_will_not_stop_holds_the_next_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    _unkillable(supervisor, monkeypatch)
    fake_manim.plan({}, later=[video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)])
    _send(supervisor, cmd="render_preview", id="preview-1")
    first = _current(supervisor)
    stdout.wait_for("preview-1", "started")
    fake_manim.wait_until_held()
    _send(supervisor, cmd="render_preview", id="preview-2")
    assert stdout.of("preview-2") == [{**QUEUED, "jobId": "preview-2"}]
    assert not first.ended
    assert _argv_count(supervisor) == 1
    fake_manim.release()
    _join(first)
    stdout.wait_for("preview-2", "done")
    ended = {**CANCELLED, "jobId": "preview-1"}
    assert stdout.of("preview-1") == [{"event": "started", "jobId": "preview-1"}, ended]
    second = [line["event"] for line in stdout.of("preview-2")]
    assert second[:2] == ["queued", "started"] and second[-1] == "done"
    assert stdout.lines.index(ended) < stdout.lines.index({"event": "started", "jobId": "preview-2"})
    assert _argv_count(supervisor) == 2


def test_an_export_waits_for_a_preview_that_will_not_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    _unkillable(supervisor, monkeypatch)
    clips = [complete_clip(".mp4")] * 4
    fake_manim.plan(
        {},
        later=[
            {**video_render(supervisor.layout, "480p15", clips), **video_render(supervisor.layout, "1080p30", clips)}
        ],
    )
    _send(supervisor, cmd="render_preview", id="preview-1")
    first = _current(supervisor)
    stdout.wait_for("preview-1", "started")
    fake_manim.wait_until_held()
    _send(supervisor, cmd="export", id="export-1")
    _send(supervisor, cmd="render_preview", id="preview-2")
    _send(supervisor, cmd="export", id="export-2")
    _send(supervisor, cmd="save_as", id="save", path=str(tmp_path))
    assert stdout.of("export-1") == [{**QUEUED, "jobId": "export-1"}]
    # The preview waits behind the held export without a queued line of its own.
    assert stdout.of("preview-2") == []
    for job_id in ("export-2", "save"):
        assert stdout.of(job_id) == [
            {"event": "rejected", "jobId": job_id, "code": "export_running", "message": "An export is already running."}
        ]
    fake_manim.release()
    _join(first)
    stdout.wait_for("preview-2", "done")
    assert [line["event"] for line in stdout.of("export-1")] == ["queued", "started", "done"]
    assert stdout.of("preview-2")[0] == {"event": "started", "jobId": "preview-2"}
    assert stdout.lines.index(stdout.of("export-1")[-1]) < stdout.lines.index(stdout.of("preview-2")[0])
    assert _argv_count(supervisor) == 3


def test_a_held_export_is_cancelled_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    _unkillable(supervisor, monkeypatch)
    fake_manim.plan({}, later=[video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)])
    _send(supervisor, cmd="render_preview", id="preview-1")
    first = _current(supervisor)
    fake_manim.wait_until_held()
    _send(supervisor, cmd="export", id="export-1")
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="export-1")
    assert stdout.of("export-1") == [{**QUEUED, "jobId": "export-1"}, {**CANCELLED, "jobId": "export-1"}]
    assert stdout.of("cancel-1") == []
    fake_manim.release()
    _join(first)
    assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}
    assert supervisor._current is None
    assert _argv_count(supervisor) == 1


@pytest.mark.parametrize("first_stop", ["render_preview", "cancel"])
def test_a_render_that_outlived_its_stop_is_not_waited_for_again(
    first_stop: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    _unkillable(supervisor, monkeypatch)
    clips = [complete_clip(".mp4")] * 4
    fake_manim.plan(
        {},
        later=[
            {**video_render(supervisor.layout, "480p15", clips), **video_render(supervisor.layout, "1080p30", clips)}
        ],
    )
    _send(supervisor, cmd="render_preview", id="preview-1")
    first = _current(supervisor)
    fake_manim.wait_until_held()
    if first_stop == "cancel":
        _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
        time.sleep(1)
    else:
        # This request waits the 1 s for preview-1 (D19).
        _send(supervisor, cmd="render_preview", id="preview-0")
    asked = time.monotonic()
    _send(supervisor, cmd="render_preview", id="preview-2")
    _send(supervisor, cmd="export", id="export-1")
    # preview-1 was told to stop over 1 s ago, so neither request waits for it again.
    assert time.monotonic() - asked < 0.5
    assert stdout.of("preview-2") == [{**QUEUED, "jobId": "preview-2"}]
    assert stdout.of("export-1") == [{**QUEUED, "jobId": "export-1"}]
    fake_manim.release()
    _join(first)
    stdout.wait_for("preview-2", "done")
    assert [line["event"] for line in stdout.of("export-1")] == ["queued", "started", "done"]
    assert _argv_count(supervisor) == 3


def test_a_cancelled_job_reads_manim_output_to_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    _unkillable(supervisor, monkeypatch)
    # More than a pipe holds, written after the cancel: a reader that stopped would block Manim for good.
    fake_manim.plan({}, [f"line {number} " + "x" * 120 for number in range(2000)], later=[{}])
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    fake_manim.wait_until_held()
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    fake_manim.release()
    _join(job)
    assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}
    assert f"manim: line 1999 {'x' * 120}" in supervisor.log_path.read_text(encoding="utf-8")


def test_a_cancelled_job_does_not_wait_for_a_child_that_holds_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)

    def kill_manim_only(job: _Job) -> None:
        assert job.proc is not None
        job.proc.kill()

    monkeypatch.setattr(supervisor, "_kill_process", kill_manim_only)
    fake_manim.plan({}, later=[{}], child=True)
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    fake_manim.wait_until_held()
    try:
        cancelled_at = time.monotonic()
        _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
        job.thread.join(timeout=10)
        # The child sleeps for a minute; the job ends without waiting for it (F036).
        assert not job.thread.is_alive()
        assert time.monotonic() - cancelled_at < 5
        assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}
    finally:
        with contextlib.suppress(OSError):
            os.kill(fake_manim.child_pid(), signal.SIGTERM)


# Progress from the files Manim writes (D13, F022, F029, F030, F032, G5.7, G5.10)


def test_progress_hands_out_each_finished_partial_in_beat_order(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "scene" / "480p15"
    folder = video / "partial_movie_files" / "MainScene"
    folder.mkdir(parents=True)
    cached = _clip(folder / "c0.mp4", 0, at=-60)
    progress = ManimProgress(video, ClipChecker(), {0: cached}, [1, 2, 3])
    # A beat whose partial is current already is ready at once.
    assert progress.poll() == [(0, cached)]
    # Manim writes one partial at a time, so the newest may still be in flight. The names do not give the order.
    second = _clip(folder / "z1.mp4", 1, at=1)
    assert progress.poll() == []
    third = _clip(folder / "a2.mp4", 2, at=2)
    assert progress.poll() == [(1, second)]
    in_flight = _clip(folder / "m3.mp4", 3, at=3, complete=False)
    assert progress.poll() == [(2, third)]
    assert progress.poll() == []
    done = _clip(in_flight, 3, at=4)
    assert progress.finish([cached, second, third, done]) == [(3, done)]


def test_progress_waits_for_the_segment_list_when_the_order_is_unclear(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "scene" / "480p15"
    folder = video / "partial_movie_files" / "MainScene"
    folder.mkdir(parents=True)
    broken = ManimProgress(video, ClipChecker(), {}, [0, 1, 2])
    cut_off = _clip(folder / "a.mp4", 0, at=1, complete=False)
    later = [_clip(folder / "b.mp4", 1, at=2), _clip(folder / "c.mp4", 2, at=3)]
    # A finished partial that is cut off ends the live order.
    assert broken.poll() == []
    assert broken.finish([cut_off, *later]) == [(0, cut_off), (1, later[0]), (2, later[1])]

    # Manim reused a partial it wrote earlier in this run, so there is one partial fewer than beats.
    reused = ManimProgress(video, ClipChecker(), {}, [0])
    extra = [_clip(folder / f"{name}.mp4", 9, at=at) for name, at in (("d", 5), ("e", 6), ("f", 7))]
    assert reused.poll() == [(0, extra[0])]
    assert reused.poll() == []
    assert reused.finish([extra[0], extra[0], extra[1], extra[2]]) == [(1, extra[0]), (2, extra[1]), (3, extra[2])]

    # Without the order of the beats Manim renders (hand-written mode), every beat waits for the list.
    handwritten = ManimProgress(video, ClipChecker(), {}, None)
    _clip(folder / "g.mp4", 9, at=8)
    _clip(folder / "h.mp4", 9, at=9)
    assert handwritten.poll() == []


def test_progress_never_orders_partials_with_one_mtime_by_name(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "scene" / "480p15"
    folder = video / "partial_movie_files" / "MainScene"
    folder.mkdir(parents=True)
    # Manim closed beat 0's partial and opened beat 1's in one clock tick, and the one in flight sorts first.
    progress = ManimProgress(video, ClipChecker(), {}, [0, 1])
    finished = _clip(folder / "f0.mp4", 0, at=1)
    in_flight = _tied(_clip(folder / "a1.mp4", 1, complete=False), finished)
    assert progress.poll() == []
    _clip(in_flight, 1, at=2)
    assert progress.poll() == [(0, finished)]
    assert progress.finish([finished, in_flight]) == [(1, in_flight)]

    # Beats 1 and 2 finished in one clock tick, named against their order, so they wait for the segment list.
    progress = ManimProgress(video, ClipChecker(), {}, [0, 1, 2, 3])
    beats = [_clip(folder / "m0.mp4", 0, at=3), _clip(folder / "z1.mp4", 1, at=4)]
    beats += [_tied(_clip(folder / "b2.mp4", 2), beats[1]), _clip(folder / "c3.mp4", 3, at=5)]
    assert progress.poll() == [(0, beats[0])]
    assert progress.poll() == []
    assert progress.finish(beats) == [(1, beats[1]), (2, beats[2]), (3, beats[3])]


def test_progress_reads_only_the_segment_list_this_run_wrote(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "scene" / "1080p30"
    folder = video / "partial_movie_files" / "MainScene"
    folder.mkdir(parents=True)
    manifest = folder / MANIFEST
    manifest.write_text("file 'file:C:/old/a.mp4'\n", encoding="utf-8")
    progress = ManimProgress(video, ClipChecker(), {}, [0, 1])
    # A list from an earlier render, or from another quality folder, is not this run's (F032).
    other = tmp_path / "videos" / "scene" / "480p15" / "partial_movie_files" / "MainScene"
    other.mkdir(parents=True)
    (other / MANIFEST).write_text("file 'file:C:/x/b.mp4'\n", encoding="utf-8")
    assert progress.segments() == []
    # Only the names are read, and each is spelled inside this run's folder (C5).
    manifest.write_text(
        "# This file records the segment order used by Manim.\n"
        "file 'file:C:/Users/O'Brien/media/one.mp4'\n"
        "file 'file:C:/elsewhere/two.webm'\n",
        encoding="utf-8",
    )
    assert progress.segments() == [folder / "one.mp4", folder / "two.webm"]


def test_a_discard_removes_only_what_this_run_wrote_that_may_be_cut_off(tmp_path: Path) -> None:
    media = tmp_path / "media"
    video = media / "videos" / "scene" / "480p15"
    folder = video / "partial_movie_files" / "MainScene"
    folder.mkdir(parents=True)
    before = [_clip(folder / "earlier.mp4", 0, at=-1), _clip(video / "MainScene.mp4", 0, at=-1)]
    (folder / MANIFEST).write_text("file 'file:earlier.mp4'\n", encoding="utf-8")
    # Another quality folder, which another window may be rendering into right now (G5.7).
    other = media / "videos" / "scene" / "1080p30" / "partial_movie_files" / "MainScene"
    other.mkdir(parents=True)
    progress = ManimProgress(video, ClipChecker(), {}, [0, 1, 2])
    elsewhere = [_clip(other / "busy.mp4", 1, at=1, complete=False), _clip(other / "done.mp4", 1, at=1)]
    finished = _clip(folder / "finished.mp4", 1, at=1)
    # A file server whose clock runs an hour behind stamps this run's file in the past (G5.10).
    cut_off = _clip(folder / "cut-off.mp4", 2, at=-3600, complete=False)
    rewritten = [_clip(video / "MainScene.mp4", 2, at=2, complete=False)]
    (folder / MANIFEST).write_text("file 'file:finished.mp4'\n", encoding="utf-8")
    removed, failed = progress.discard()
    assert sorted(removed) == sorted([cut_off, folder / MANIFEST, *rewritten])
    assert failed == []
    assert all(path.is_file() for path in [before[0], finished, *elsewhere])


def test_a_file_the_discard_cannot_remove_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "videos" / "scene" / "480p15"
    folder = video / "partial_movie_files" / "MainScene"
    folder.mkdir(parents=True)
    progress = ManimProgress(video, ClipChecker(), {}, [0])
    held = _clip(folder / "held.mp4", 0, at=1, complete=False)
    loose = _clip(folder / "loose.mp4", 0, at=2, complete=False)
    refused = PermissionError(13, IN_USE)

    def in_use(path: Path) -> None:
        if path == held:
            raise refused
        remove_file(path)

    monkeypatch.setattr(progress_module, "remove_file", in_use)
    assert progress.discard() == ([loose], [(held, refused)])


@pytest.mark.parametrize(("winerror", "attempts"), [(32, 3), (None, 1)], ids=["in-use", "other"])
def test_remove_file_waits_only_while_windows_reports_the_file_in_use(
    winerror: int | None, attempts: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = write_clip(tmp_path / "cut-off.mp4")
    calls: list[Path] = []
    real_unlink = Path.unlink

    def busy(path: Path, missing_ok: bool = False) -> None:
        calls.append(path)
        if len(calls) < 3:
            refused = PermissionError(13, IN_USE)
            refused.winerror = winerror
            raise refused
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    with pytest.raises(PermissionError) if winerror is None else contextlib.nullcontext():
        remove_file(target)
    monkeypatch.undo()
    assert (len(calls), target.exists()) == (attempts, winerror is None)


@pytest.mark.usefixtures("quick_kill")
def test_a_cancelled_preview_keeps_the_partials_it_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    folder = supervisor.layout.partial_movie_dir("480p15")
    folder.mkdir(parents=True)
    earlier = _clip(folder / ("e" * 64 + ".mp4"), 9, at=-1)
    partials = [folder / f"{index:064x}.mp4" for index in range(3)]
    # Beats 0 and 1 are finished; beat 2 is still being written when the cancel comes.
    written = {partials[0]: _bytes(0), partials[1]: _bytes(1), partials[2]: _bytes(2)[:-8]}
    fake_manim.plan(written, hold=True)
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    _wait_for_beats(stdout, "preview-1", 2)
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    _join(job)
    assert [line["event"] for line in stdout.of("preview-1")] == ["started", "beat_ready", "beat_ready", "error"]
    assert [path.exists() for path in partials] == [True, True, False]
    assert earlier.is_file()
    index = _index(supervisor)
    assert [(beat.partial, beat.fresh) for beat in index.beats[:3]] == [
        (str(partials[0]), True),
        (str(partials[1]), True),
        (None, False),
    ]


# beat_ready (C7.1, D17, F135)


@pytest.mark.parametrize("change", ["edit", "undo"])
def test_beat_ready_stops_once_the_document_changes(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    if change == "undo":
        _send(supervisor, cmd="edit", id="before", edit=_color("#FF0000"))
    files = video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)
    partials = [path for path in files if path.suffix == ".mp4" and "partial_movie_files" in path.parts]
    # Beat 0 is finished once beat 1's partial exists; then the fake waits.
    first = {path: files.pop(path) for path in partials[:2]}
    fake_manim.plan(first, later=[files])
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    ready = stdout.wait_for("preview-1", "beat_ready")
    fake_manim.wait_until_held()
    if change == "edit":
        _send(supervisor, cmd="edit", id="change", edit=_color("#00FF00"))
    else:
        _send(supervisor, cmd="undo", id="change")
    reply = stdout.last()
    fake_manim.release()
    _join(job)
    assert (ready["beatIndex"], reply["event"]) == (0, "state")
    lines = stdout.read()
    after = lines[lines.index(reply) + 1 :]
    assert [line["event"] for line in after if line["jobId"] == "preview-1"] == ["done"]
    # No state line closes a preview (C5).
    assert [line["event"] for line in stdout.of("preview-1")] == ["started", "beat_ready", "done"]
    beats = json.loads(supervisor.layout.index_path.read_text(encoding="utf-8"))["beats"]
    assert [beat["fresh"] for beat in beats] == [True, False, False, False]


def test_an_export_sends_no_beat_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    fake_manim.plan(video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="export", id="export-1")
    assert [line["event"] for line in stdout.of("export-1")] == ["started", "done"]
    assert all(beat.partial is None for beat in _index(supervisor).beats)


# The preview index (F010, F034, F135)


def test_the_full_movie_is_current_only_for_the_document_it_shows() -> None:
    session = Session(empty_project())
    session.edit({"op": "add_formula", "afterBeatId": None})
    session.mark_rendered("preview")
    assert session.output_current("preview")
    session.edit({"op": "add_text", "afterBeatId": session.project.scene.beats[-1].id})
    session.mark_rendered("preview")
    session.undo()
    # Every remaining beat matches the render, but the movie also shows the removed one (F010).
    assert session.dirty_flags("preview") == [False] * len(session.project.scene.beats)
    assert not session.output_current("preview")


def test_undoing_an_appended_beat_drops_the_full_movie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    _send(supervisor, cmd="edit", id="append", edit={"op": "add_text", "afterBeatId": "beat_4"})
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 5))
    _run(supervisor, cmd="render_preview", id="preview-1")
    _send(supervisor, cmd="get_state", id="rendered")
    assert stdout.last()["index"]["output"] is not None
    _send(supervisor, cmd="undo", id="undo")
    index = stdout.last()["index"]
    assert [beat["fresh"] for beat in index["beats"]] == [True] * 4
    assert index["output"] is None


def test_a_beat_shows_its_newest_render_and_undo_brings_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    folder = supervisor.layout.partial_movie_dir("480p15")
    folder.mkdir(parents=True)
    supervisor._report_beats(_prepared(supervisor), [(index, _clip(folder / f"a{index}.mp4", 0)) for index in range(4)])
    _send(supervisor, cmd="edit", id="red", edit=_color("#FF0000"))
    supervisor._report_beats(
        _prepared(supervisor), [(index, _clip(folder / f"b{index}.mp4", 1)) for index in (1, 2, 3)]
    )
    _send(supervisor, cmd="edit", id="green", edit=_color("#00FF00"))
    _prepared(supervisor)
    # The last good copy is the render just before the edit, not one two edits old (F034).
    last_good = supervisor.layout.last_good_dir / "beat_2.mp4"
    assert last_good.read_bytes() == _bytes(1)
    _send(supervisor, cmd="undo", id="undo-red")
    beat = stdout.last()["index"]["beats"][1]
    assert (beat["fresh"], beat["partial"]) == (True, str(last_good))
    # The red render does not show the blue document as current (F135).
    _send(supervisor, cmd="undo", id="undo-blue")
    beat = stdout.last()["index"]["beats"][1]
    assert (beat["fresh"], beat["partial"], beat["lastGood"]) == (False, None, str(last_good))


def test_a_job_of_an_earlier_binding_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "first"), monkeypatch)
    earlier = _prepared(supervisor)
    # The second project's bytes are the same, so only the binding generation tells the jobs apart.
    _send(supervisor, cmd="open_project", id="open", path=str(_journey(tmp_path / "second")))
    clip = write_clip(_made(supervisor.layout.partial_movie_dir("480p15") / f"{'a' * 64}.mp4"))
    supervisor._report_beats(earlier, [(0, clip)])
    assert _index(supervisor).beats[0].partial is None
    supervisor._report_beats(_prepared(supervisor), [(0, clip)])
    assert _index(supervisor).beats[0].partial == str(clip)
    assert [line["event"] for line in stdout.read() if line["jobId"] == "preview-1"] == ["beat_ready"]


# Publishing an export (C7.2, D56, F031, F164, G1.2)


def test_re_export_skips_the_export_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    fake_manim.plan(video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="export", id="export-1")
    _run(supervisor, cmd="export", id="export-2")
    _run(supervisor, cmd="export", id="export-3", force=True)
    done = stdout.of("export-1")[-1]
    assert done["current"] is True
    assert stdout.of("export-2") == [{**done, "jobId": "export-2"}]
    assert stdout.of("export-3") == [{"event": "started", "jobId": "export-3"}, {**done, "jobId": "export-3"}]
    assert _argv_count(supervisor) == 2


def test_an_export_of_another_profile_renders_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    clips = [complete_clip(".mp4")] * 4
    fake_manim.plan(
        {**video_render(supervisor.layout, "1080p30", clips), **video_render(supervisor.layout, "1080p60", clips)}
    )
    _run(supervisor, cmd="export", id="export-1")
    profile = {"format": "mp4", "width": 1920, "height": 1080, "fps": 60, "transparent": False}
    _send(supervisor, cmd="edit", id="fps", edit={"op": "set_export_profile", **profile})
    _run(supervisor, cmd="export", id="export-2")
    # The export cache misses, so Manim renders the new profile (F136).
    assert [line["event"] for line in stdout.of("export-2")] == ["started", "done"]
    first, second = (Path(stdout.of(job_id)[-1]["path"]) for job_id in ("export-1", "export-2"))
    assert first.parent != second.parent
    assert stdout.of("export-2")[-1]["current"] is True
    first_argv, second_argv = _argvs(supervisor)
    assert (first_argv[first_argv.index("--fps") + 1], second_argv[second_argv.index("--fps") + 1]) == ("30", "60")


def test_a_re_export_that_cannot_replace_the_file_keeps_the_older_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    files = video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4)
    fake_manim.plan(files)
    _run(supervisor, cmd="export", id="export-1")
    published = Path(stdout.of("export-1")[-1]["path"])
    kept = {path: path.read_bytes() for path in (published, published.parent / "export.json")}
    movie = supervisor.layout.media_dir / "videos" / "scene" / "1080p30" / "MainScene.mp4"
    fake_manim.plan({**files, movie: _bytes(7)})
    real_replace = os.replace

    def in_use(source: str, target: str) -> None:
        # Windows refuses to replace a file a player holds open.
        if "exports" in Path(target).parts:
            raise PermissionError(13, IN_USE, str(target))
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", in_use)
    _run(supervisor, cmd="export", id="export-2", force=True)
    assert stdout.of("export-2")[-1] == {
        "event": "error",
        "jobId": "export-2",
        "code": "publish_failed",
        "message": (
            f"Could not save the export to {published}: {IN_USE}. "
            "If the file is open in another program, close it and export again."
        ),
    }
    assert {path: path.read_bytes() for path in kept} == kept
    assert sorted(path.name for path in published.parent.iterdir()) == ["export.json", "scene.mp4"]


def test_an_edit_does_not_wait_for_an_export_to_be_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    fake_manim.plan(video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4))
    copying, copied = threading.Event(), threading.Event()

    def slow_copy(source: Path, target: Path) -> None:
        copying.set()
        assert copied.wait(30)
        copy_atomically(source, target)

    monkeypatch.setattr(exports_module, "copy_atomically", slow_copy)
    _send(supervisor, cmd="export", id="export-1")
    job = _current(supervisor)
    assert copying.wait(30)
    # The copy holds no document lock, so the edit is answered while it runs (F031, F164).
    _send(supervisor, cmd="edit", id="recolor", edit=_color("#FF0000"))
    assert stdout.last()["jobId"] == "recolor"
    copied.set()
    _join(job)
    done = stdout.of("export-1")[-1]
    assert (done["event"], done["current"]) == ("done", False)


def test_a_folder_is_published_whole_or_not_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "exports" / "frames"
    target.mkdir(parents=True)
    (target / "0000.png").write_bytes(b"old")
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(2):
        (frames / f"{index:04d}.png").write_bytes(b"new")
    real_replace = project_folder.replace

    def refused(source: Path, destination: Path) -> None:
        if source.name.endswith(".tmp"):
            raise PermissionError(13, IN_USE)
        real_replace(source, destination)

    with monkeypatch.context() as patched:
        patched.setattr(project_folder, "replace", refused)
        with pytest.raises(PermissionError):
            copy_atomically(frames, target)
    assert [(path.name, path.read_bytes()) for path in tmp_path.joinpath("exports").rglob("*.png")] == [
        ("0000.png", b"old")
    ]
    assert [path.name for path in target.parent.iterdir()] == ["frames"]
    copy_atomically(frames, target)
    assert sorted(path.name for path in target.iterdir()) == ["0000.png", "0001.png"]
    assert [path.name for path in target.parent.iterdir()] == ["frames"]


# render_failed (D11, D23, F016, F017, F095)


def test_a_tex_error_shows_the_error_line_the_bad_command_and_where_tex_stopped() -> None:
    output = _tex_failure_output("Undefined control sequence.")
    undefined = (CAPTURES / "undefined-control-sequence.log").read_text(encoding="utf-8")
    # MathTex wraps the formula in align*, so only the <argument> line names \sinn.
    assert format_render_failure(1, output, undefined) == UNDEFINED_SINN
    # A context line other than <argument>, here TeX's <read *>, is left out.
    missing = (CAPTURES / "missing-package.log").read_text(encoding="utf-8")
    assert format_render_failure(1, output, missing) == (
        "LaTeX failed.\n! LaTeX Error: File `qrcode.sty' not found.\nl.6 \\begin"
    )
    assert tex_error_lines("This is pdfTeX\nOutput written on scene.dvi (1 page).\n") == []


def test_an_argument_line_counts_only_before_where_tex_stopped() -> None:
    first = ["! Missing $ inserted.", "<inserted text>", "l.9 \\end{align*}"]
    later = ["! Undefined control sequence.", "<argument> \\sinn", "l.9 \\end{align*}"]
    assert tex_error_lines("\n".join(first + later)) == ["! Missing $ inserted.", "l.9 \\end{align*}"]
    assert tex_error_lines("! Emergency stop.\n<argument> \\sinn \n") == ["! Emergency stop."]


def test_a_tex_failure_without_a_log_shows_manims_exception() -> None:
    output = (CAPTURES / "latex-without-log.txt").read_text(encoding="utf-8")
    assert format_render_failure(1, output) == (
        "LaTeX failed.\nRuntimeError: latex failed but did not produce a log file. Check your LaTeX installation."
    )


def test_a_failure_is_latex_only_when_manim_says_so() -> None:
    # A real Manim 0.22 traceback, from a project under C:\Users\j.logan\Documents\LaTeX (F017).
    traceback = (CAPTURES / "runtime-error.txt").read_text(encoding="utf-8")
    assert "LaTeX" in traceback and ".log" in traceback
    assert format_render_failure(1, traceback) == (
        "manim exited with status 1: RuntimeError: phase 1 scene raised RuntimeError"
    )
    code_context = "│ > 12 │   │   plot = axes.plot(lambda x: np.log(x))  │\n└────┘\nValueError: math domain error\n"
    assert format_render_failure(1, code_context) == "manim exited with status 1: ValueError: math domain error"
    division = "INFO  Writing C:\\Users\\j.logan\\latex.log\nZeroDivisionError: division by zero\n"
    assert format_render_failure(1, division) == "manim exited with status 1: ZeroDivisionError: division by zero"
    assert format_render_failure(3221225477, "") == "manim exited with status 3221225477"
    assert (
        format_render_failure(1, "│ frame │\nKilled by the user\n") == "manim exited with status 1: Killed by the user"
    )


def test_a_failed_render_reads_the_tex_log_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    tex = supervisor.layout.media_dir / "Tex"
    tex.mkdir(parents=True)
    # A log from an earlier compile is not this run's.
    (tex / "earlier.log").write_bytes((CAPTURES / "missing-package.log").read_bytes())
    written = {tex / "formula.log": (CAPTURES / "undefined-control-sequence.log").read_bytes()}
    output = _tex_failure_output("Undefined control sequence.").splitlines()
    fake_manim.plan(written, output, exit_code=1)
    _run(supervisor, cmd="render_preview", id="preview-1")
    failed = stdout.of("preview-1")[-1]
    assert (failed["event"], failed["code"]) == ("error", "render_failed")
    assert failed["message"] == UNDEFINED_SINN
    # The details are the last lines Manim wrote; worker.log keeps them all (D23).
    assert failed["details"].splitlines() == output


@pytest.mark.parametrize(("command", "job_id"), [("render_preview", "preview-1"), ("export", "export-1")])
def test_a_render_that_exits_0_without_a_movie_fails(
    command: str, job_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    fake_manim.plan({})
    _run(supervisor, cmd=command, id=job_id)
    assert stdout.of(job_id) == [
        {"event": "started", "jobId": job_id},
        {"event": "error", "jobId": job_id, "code": "render_failed", "message": "manim exited 0 without a movie"},
    ]
    assert not supervisor.layout.exports_dir.exists()
    index = _index(supervisor)
    assert index.output is None
    assert all(beat.partial is None for beat in index.beats)


@pytest.mark.parametrize(("command", "job_id"), [("render_preview", "preview-1"), ("export", "export-1")])
def test_a_manim_that_cannot_start_ends_the_job_without_started(
    command: str, job_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)

    def missing(*args: object, **kwargs: object) -> NoReturn:
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr(supervisor_module.subprocess, "Popen", missing)
    _run(supervisor, cmd=command, id=job_id)
    # The terminal line is the job's only line: Manim never started (C7.2).
    (failed,) = stdout.of(job_id)
    assert (failed["event"], failed["code"]) == ("error", "render_failed")
    assert failed["message"].startswith("Could not start Manim: ")
    assert not supervisor.layout.run_marker.exists()
    _send(supervisor, cmd="status", id="status-1")
    assert stdout.last() == {"event": "ready", "jobId": "status-1", "protocol": 2, "startupError": None}


def _tex_failure_output(error: str) -> str:
    """What Manim writes when a TeX compile fails: its log line through Rich, then the exception."""
    return (
        "[10/24/26 10:00:00] ERROR    LaTeX compilation      tex_file_writing.py:320\n"
        f"                             error: {error}\n"
        "ValueError: latex error converting to dvi. See log output above or the log \n"
        "file: C:\\Users\\j.logan\\project\\.mathimator\\cache\\media\\Tex\\formula.log\n"
    )


def _supervisor(project: Path, monkeypatch: pytest.MonkeyPatch) -> Supervisor:
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    return Supervisor(project, sys.executable, project.parent / f"{project.name}.log", False)


def _unkillable(supervisor: Supervisor, monkeypatch: pytest.MonkeyPatch) -> None:
    """A render whose kill does nothing, so it outlives the 1 s its stop waits (D19).

    A subprocess fake cannot ignore a real kill, so this stands in for one that will not stop.
    """
    monkeypatch.setattr(supervisor, "_kill_process", lambda job: None)


def _journey(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "project.json").write_bytes(JOURNEY.read_bytes())
    return root


def _made(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _bytes(number: int) -> bytes:
    """A complete MP4 whose content tells it from the others."""
    return iso_box(b"ftyp", b"isom" + bytes(4)) + iso_box(b"mdat", bytes([number]) * 64) + iso_box(b"moov")


def _clip(path: Path, number: int, *, at: float = 0, complete: bool = True) -> Path:
    """Write a clip whose mtime is ``at`` seconds from now. A clip that is not complete lacks its moov box."""
    data = _bytes(number)
    path.write_bytes(data if complete else data[:-8])
    stamp = time.time_ns() + int(at * 1e9)
    os.utime(path, ns=(stamp, stamp))
    return path


def _tied(path: Path, other: Path) -> Path:
    """Give ``path`` the mtime of ``other``, as when Manim wrote both in one clock tick."""
    stamp = other.stat().st_mtime_ns
    os.utime(path, ns=(stamp, stamp))
    return path


def _color(color: str) -> dict[str, object]:
    return {"op": "set_object_color", "objectId": "plot_1", "color": color}


def _send(supervisor: Supervisor, **request: object) -> None:
    supervisor.handle_line(json.dumps(request))


def _run(supervisor: Supervisor, **request: object) -> None:
    """Send a request and wait for the job it starts, if it starts one."""
    _send(supervisor, **request)
    job = supervisor._current
    if job is not None:
        _join(job)


def _current(supervisor: Supervisor) -> _Job:
    job = supervisor._current
    assert job is not None
    return job


def _join(job: _Job) -> None:
    job.thread.join(timeout=60)
    assert not job.thread.is_alive()


def _prepared(supervisor: Supervisor) -> _Job:
    """A preview job prepared under the current binding and document, as its thread would prepare it."""
    job = _Job("preview-1", "preview", threading.Thread(), threading.Event())
    with supervisor._doc_lock:
        assert supervisor._prepare_job_locked(job)
    return job


def _index(supervisor: Supervisor) -> PreviewIndex:
    assert supervisor._index is not None
    return supervisor._index


def _wait_for_beats(stdout: Stdout, job_id: str, count: int) -> None:
    deadline = time.monotonic() + 30
    while len([line for line in stdout.of(job_id) if line["event"] == "beat_ready"]) < count:
        assert time.monotonic() < deadline, stdout.lines
        time.sleep(0.02)


def _argv_count(supervisor: Supervisor) -> int:
    return supervisor.log_path.read_text(encoding="utf-8").count("argv: ")


def _argvs(supervisor: Supervisor) -> list[list[str]]:
    """Each Manim command line the supervisor logged, in order."""
    lines = supervisor.log_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line.split("argv: ", 1)[1]) for line in lines if "argv: " in line]
