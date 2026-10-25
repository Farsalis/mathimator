"""Phase 1 worker tests from the design spec.

The tests marked ``render`` start real Manim processes. They are slow on purpose.
The supervisor now takes a project directory. Handwritten scenes are copied to
``.mathimator/codegen/scene.py`` and rendered when the folder has no
``project.json``.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mathimator_worker.argv import build_manim_argv
from mathimator_worker.process_group import (
    descendant_pids,
    process_alive,
    process_name,
    terminate_process_group,
    warm_process_table,
)
from tests.support import (
    LAST_BEAT,
    OUT,
    REFERENCE,
    REPO,
    RUNTIME_ERROR,
    TERMINAL_EVENTS,
    WORKER,
    SupervisorClient,
    assert_phase1_argv,
    fixture_project,
    frame_count,
    handwritten_project,
    manim_pids,
    media_dir,
    probe_video,
    read_argv,
    scene_path,
    sha256_file,
    start_supervisor,
)

PREVIEW_TIMEOUT_S = 60 * 60
EXPORT_TIMEOUT_S = 3 * 60 * 60
# What Manim runs for a formula or an axis label.
TEX_PROGRAMS = frozenset({"latex", "dvisvgm", "pdftex", "xelatex"})
# Twenty beats of 0.5 s, plays and waits, with no formula and no axis labels.
FRACTIONAL = REPO / "tests" / "fixtures" / "fractional-10" / "project.json"


def test_worker_source_has_no_manim_av_or_win32() -> None:
    files = list((WORKER / "mathimator_worker").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [module.split(".", 1)[0]]
            else:
                continue
            assert "manim" not in names, path
            assert "av" not in names, path
    assert "pywin32" not in text
    assert "win32api" not in text
    assert "taskkill" not in text
    job_object = (WORKER / "mathimator_worker" / "win32_job.py").read_text(encoding="utf-8")
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in job_object
    for path in files:
        body = path.read_text(encoding="utf-8")
        # The Job Object, and the 8.3 alias of a project path that TeX cannot take (D61).
        if path.name in {"win32_job.py", "manim_paths.py"}:
            continue
        assert "ctypes" not in body, path


def test_importing_supervisor_does_not_load_manim_or_av() -> None:
    code = (
        "import sys\n"
        "import mathimator_worker.supervisor\n"
        "bad = [name for name in sys.modules if name.split('.', 1)[0] in {'manim', 'av'}]\n"
        "print(' '.join(bad))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(WORKER)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == ""


def test_preview_argv_matches_section_5() -> None:
    argv = build_manim_argv("/opt/py/bin/python", "preview")
    assert argv == [
        "/opt/py/bin/python",
        "-P",
        "--check-hash-based-pycs",
        "always",
        "-m",
        "mathimator_worker.manim_main",
        "render",
        "scene.py",
        "MainScene",
        "--config_file",
        str(WORKER / "mathimator_worker" / "manim.cfg"),
        "--silent",
        "--renderer",
        "cairo",
        "--seed",
        "1",
        "-r",
        "854,480",
        "--fps",
        "15",
        "--format",
        "mp4",
        "--media_dir",
        "../cache/media",
        "--max-inflight-encoders",
        "1",
        "--progress_bar",
        "none",
    ]
    assert_phase1_argv(argv, width=854, height=480, fps=15)


def test_export_argv_changes_resolution_and_fps() -> None:
    argv = build_manim_argv("/opt/py/bin/python", "export")
    assert_phase1_argv(argv, width=1920, height=1080, fps=30)
    assert argv[argv.index("-r") + 1] == "1920,1080"
    assert argv[argv.index("--fps") + 1] == "30"


def test_last_beat_scene_differs_only_in_final_color() -> None:
    original = REFERENCE.read_text(encoding="utf-8")
    variant = LAST_BEAT.read_text(encoding="utf-8")
    assert original != variant
    assert original.replace("#F2CC8F", "#81B29A") == variant
    assert "run_time=8" in original
    assert "run_time=10" in original
    assert "run_time=6" in original
    assert "self.wait(4)" in original
    assert "self.wait(8)" in original
    assert 'set_color("#E07A5F")' in original
    assert "np.array([0.0, -0.4, 0.0])" in original


@pytest.mark.process_table
def test_process_group_kills_a_child_within_one_second() -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(300)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    child_pid = int(proc.stdout.readline().strip())
    # The worker starts the Windows process table helper at startup, so a kill never waits for it.
    warm_process_table()
    started = time.perf_counter()
    terminate_process_group(proc)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    assert not process_alive(proc.pid)
    assert not process_alive(child_pid)


@pytest.mark.render
def test_runtime_error_then_status() -> None:
    project = handwritten_project(OUT / "runtime-error-project", RUNTIME_ERROR)
    log_path = OUT / "logs" / "runtime-error.log"
    client = start_supervisor(project, log_path)
    try:
        client.send({"cmd": "status", "id": "status-before"})
        ready = client.wait_for(
            lambda event: event.get("event") == "ready" and event.get("jobId") == "status-before",
            30,
        )
        assert ready["event"] == "ready"
        client.send({"cmd": "render_preview", "id": "job-error"})
        failed = client.wait_for_end("job-error", 600)
        assert failed["event"] == "error"
        assert "RuntimeError" in str(failed["message"])
        assert client.proc.poll() is None
        client.send({"cmd": "status", "id": "status-after"})
        again = client.wait_for(
            lambda event: event.get("event") == "ready" and event.get("jobId") == "status-after",
            30,
        )
        assert again["event"] == "ready"
        assert client.proc.poll() is None
    finally:
        client.close()


@pytest.mark.render
@pytest.mark.process_table
def test_cancel_ends_grandchild_within_one_second() -> None:
    project = handwritten_project(OUT / "cancel-project", REFERENCE)
    log_path = OUT / "logs" / "cancel.log"
    if log_path.exists():
        log_path.unlink()
    client = start_supervisor(project, log_path)
    try:
        client.send({"cmd": "status", "id": "status-before"})
        client.wait_for(
            lambda event: event.get("event") == "ready" and event.get("jobId") == "status-before",
            30,
        )
        client.send({"cmd": "render_preview", "id": "job-cancel"})
        argv = read_argv(log_path, timeout=60)
        assert_phase1_argv(argv, width=854, height=480, fps=15)

        manim_pid, tex = _wait_for_tex_child(client, log_path, "job-cancel")
        watched = {manim_pid, *descendant_pids(manim_pid)}
        # The 1 s rule is about TeX, so a TeX child must still run when the cancel goes out (F142).
        running_tex = [pid for pid in tex if pid in watched and process_alive(pid)]
        assert running_tex, f"the TeX children {tex} ended before the cancel"
        started = time.perf_counter()
        client.send({"cmd": "cancel", "id": "cancel-1", "jobId": "job-cancel"})
        deadline = started + 1.0
        while time.perf_counter() < deadline:
            if all(not process_alive(pid) for pid in watched):
                break
            time.sleep(0.01)
        else:
            still = [pid for pid in watched if process_alive(pid)]
            pytest.fail(f"process group still alive 1s after cancel: {still}")
        elapsed = time.perf_counter() - started
        assert elapsed < 1.0

        client.send({"cmd": "status", "id": "status-after-cancel"})
        client.wait_for(
            lambda event: event.get("event") == "ready" and event.get("jobId") == "status-after-cancel",
            30,
        )
        assert client.proc.poll() is None
        result = {
            "cancel_seconds": elapsed,
            "observed_tex": sorted(set(tex.values())),
            "running_tex_pids": running_tex,
            "watched_pids": sorted(watched),
            "manim_pid": manim_pid,
        }
        _write_json(OUT / "cancel-result.json", result)
    finally:
        client.close()


@pytest.mark.render
def test_reference_60_preview_partials_and_duration(preview: dict[str, object]) -> None:
    beats = preview["beats"]
    assert isinstance(beats, list)
    assert [event["beatIndex"] for event in beats] == list(range(8))
    paths = [Path(str(event["path"])) for event in beats]
    assert len(set(paths)) == 8
    for path in paths:
        assert path.is_file()
        assert "partial_movie_files" in path.parts
        assert path.suffix == ".mp4"
    output = Path(str(preview["output"]))
    duration, width, height = probe_video(output)
    assert (width, height) == (854, 480)
    assert abs(duration - 60.0) <= (1.0 / 15.0)
    argv = preview["argv"]
    assert isinstance(argv, list)
    assert_phase1_argv(argv, width=854, height=480, fps=15)
    record = {
        "preview_seconds": preview["seconds"],
        "output": str(output),
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "partials": [str(path) for path in paths],
        "hashes": [sha256_file(path) for path in paths],
    }
    _write_json(OUT / "preview-result.json", record)
    assert isinstance(preview["seconds"], float)


@pytest.mark.render
def test_last_beat_keeps_the_first_seven_partial_hashes(preview: dict[str, object]) -> None:
    project = Path(str(preview["project"]))
    scene = Path(str(preview["scene"]))
    first_beats = preview["beats"]
    assert isinstance(first_beats, list)
    first_hashes = [sha256_file(Path(str(event["path"]))) for event in first_beats]
    original = scene.read_bytes()
    try:
        shutil.copyfile(LAST_BEAT, scene)
        second = _render(
            project,
            OUT / "logs" / "cache.log",
            "preview",
            "job-cache",
            PREVIEW_TIMEOUT_S,
        )
    finally:
        scene.write_bytes(original)

    second_beats = second["beats"]
    assert isinstance(second_beats, list)
    assert [event["beatIndex"] for event in second_beats] == list(range(8))
    second_hashes = [sha256_file(Path(str(event["path"]))) for event in second_beats]
    assert first_hashes[:7] == second_hashes[:7]
    assert first_hashes[7] != second_hashes[7]
    _write_json(
        OUT / "cache-result.json",
        {
            "first_hashes": first_hashes,
            "second_hashes": second_hashes,
            "first_partials": [str(event["path"]) for event in first_beats],
            "second_partials": [str(event["path"]) for event in second_beats],
            "seconds": second["seconds"],
        },
    )


@pytest.mark.render
def test_half_second_beats_draw_the_frames_of_the_timeline() -> None:
    """D63, G3.5: each beat starts on the frame nearest its start, a half frame rounding up.

    At 15 fps a 0.5 s beat is 7.5 frames, so the beats are 8 and 7 frames in
    turn, plays and waits alike, and the preview is 150 frames.
    """
    project = fixture_project(OUT / "fractional-project", FRACTIONAL)
    rendered = _render(project, OUT / "logs" / "fractional.log", "preview", "job-fractional", PREVIEW_TIMEOUT_S)
    beats = rendered["beats"]
    assert isinstance(beats, list)
    assert [frame_count(Path(str(event["path"]))) for event in beats] == [8, 7] * 10
    assert frame_count(Path(str(rendered["output"]))) == 150


@pytest.mark.render
@pytest.mark.export
def test_export_1080p30() -> None:
    project = handwritten_project(OUT / "export-project", REFERENCE)
    rendered_media = media_dir(project)
    if rendered_media.exists():
        for quality in rendered_media.glob("videos/*/1080p30"):
            shutil.rmtree(quality)
    rendered = _render(
        project,
        OUT / "logs" / "export.log",
        "export",
        "job-export",
        EXPORT_TIMEOUT_S,
    )
    output = Path(str(rendered["output"]))
    duration, width, height = probe_video(output)
    assert (width, height) == (1920, 1080)
    assert abs(duration - 60.0) <= (1.0 / 30.0)
    argv = rendered["argv"]
    assert isinstance(argv, list)
    assert_phase1_argv(argv, width=1920, height=1080, fps=30)
    _write_json(
        OUT / "export-result.json",
        {
            "export_seconds": rendered["seconds"],
            "output": str(output),
            "duration_seconds": duration,
            "width": width,
            "height": height,
        },
    )


@pytest.fixture(scope="module")
def preview() -> dict[str, object]:
    project = handwritten_project(OUT / "reference-project", REFERENCE)
    log_path = OUT / "logs" / "preview.log"
    videos = media_dir(project) / "videos"
    if videos.exists():
        shutil.rmtree(videos)
    if log_path.exists():
        log_path.unlink()
    rendered = _render(project, log_path, "preview", "job-preview", PREVIEW_TIMEOUT_S)
    rendered["project"] = str(project)
    rendered["scene"] = str(scene_path(project))
    rendered["media"] = str(media_dir(project))
    return rendered


def _render(
    project: Path,
    log_path: Path,
    profile: str,
    job_id: str,
    timeout: float,
) -> dict[str, object]:
    if log_path.exists():
        log_path.unlink()
    client: SupervisorClient = start_supervisor(project, log_path)
    try:
        client.send({"cmd": "status", "id": f"status-{job_id}"})
        client.wait_for(
            lambda event: event.get("event") == "ready" and event.get("jobId") == f"status-{job_id}",
            30,
        )
        command = "render_preview" if profile == "preview" else "export"
        started = time.perf_counter()
        client.send({"cmd": command, "id": job_id})
        finished = client.wait_for_end(job_id, timeout)
        elapsed = time.perf_counter() - started
        if finished.get("event") != "done":
            raise AssertionError(json.dumps(finished, indent=2))
        beats = [
            event for event in client.events if event.get("event") == "beat_ready" and event.get("jobId") == job_id
        ]
        argv = read_argv(log_path, timeout=5)
        return {
            "output": finished["path"],
            "beats": beats,
            "seconds": elapsed,
            "argv": argv,
        }
    finally:
        client.close()


def _wait_for_tex_child(client: SupervisorClient, log_path: Path, job_id: str) -> tuple[int, dict[int, str]]:
    """Manim's pid and its running TeX children by name, as soon as one runs.

    Fails when the render ends first: a cancel that meets no TeX child would
    leave the 1 s rule for TeX untested (F142).
    """
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        started = manim_pids(log_path)
        if started:
            manim_pid = started[-1]
            names = {pid: process_name(pid) for pid in descendant_pids(manim_pid)}
            tex = {pid: name for pid, name in names.items() if name in TEX_PROGRAMS and process_alive(pid)}
            if tex:
                return manim_pid, tex
        ended = [
            event for event in client.events if event.get("jobId") == job_id and event.get("event") in TERMINAL_EVENTS
        ]
        assert not ended, f"no TeX child was seen while the render ran: {ended}"
        time.sleep(0.02)
    raise AssertionError("no TeX child started within 180 s")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
