"""Helpers shared by every test module.

Repo paths, the render interpreter, the supervisor client, the reader of an
in-process supervisor's stdout, fake interpreters, the fake ``manim`` package,
minimal complete clips, unreadable files, links, and video probes. ``OUT``
and the scene paths belong to the phase 1 tests.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from mathimator_worker.jsonio import loads
from mathimator_worker.layout import ProjectLayout
from mathimator_worker.manim_main import concat_manifest_bytes

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
SCENES = REPO / "scenes"
OUT = REPO / "out" / "phase1"
REFERENCE = SCENES / "reference_60.py"
LAST_BEAT = SCENES / "reference_60_last_beat.py"
RUNTIME_ERROR = SCENES / "runtime_error.py"
WIN32_FIXTURES = REPO / "tests" / "fixtures" / "win32"
# The lines that end a job; exactly one of them does (C7.1).
TERMINAL_EVENTS = frozenset({"done", "error", "rejected"})
# The stderr lines a SupervisorClient keeps for its failure messages.
_STDERR_LINES = 200
# The links to a folder a test makes: junctions and symbolic links on Windows, symbolic links elsewhere.
LINK_KINDS = ("junction", "symlink") if sys.platform == "win32" else ("symlink",)


def venv_python() -> Path:
    override = os.environ.get("MATHIMATOR_PYTHON")
    if override:
        return Path(override)
    if os.name == "nt":
        candidates = [
            REPO / ".venv" / "Scripts" / "python.exe",
            REPO / ".venv" / "bin" / "python",
        ]
    else:
        candidates = [
            REPO / ".venv" / "bin" / "python",
            REPO / ".venv" / "bin" / "python3",
            REPO / ".venv" / "Scripts" / "python.exe",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def write_fake_interpreter(path: Path, sleep_s: float) -> Path:
    """Return an executable that ignores its arguments and sleeps.

    Unix can exec a shebang script. Windows ``CreateProcess`` cannot, so the
    fake interpreter there is the compiled ``sleeper.cs`` fixture, which reads
    its duration from the ``.ms`` file beside it.
    """
    if os.name != "nt":
        path.write_text(
            f"#!/usr/bin/env python3\nimport time\ntime.sleep({sleep_s})\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path
    path.with_suffix(".ms").write_text(str(max(1, int(sleep_s * 1000))), encoding="utf-8")
    return compile_csharp(WIN32_FIXTURES / "sleeper.cs", path.with_suffix(".exe"))


def compile_csharp(source: Path, exe: Path) -> Path:
    """Compile one C# file with the .NET Framework compiler that ships with Windows."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    compiler = Path(windir) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    completed = subprocess.run(
        [str(compiler), "/nologo", "/t:exe", f"/out:{exe}", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not exe.is_file():
        detail = (completed.stdout + completed.stderr).strip()
        raise RuntimeError(f"csc failed for {exe}: {detail}")
    return exe


def iso_box(kind: bytes, payload: bytes = b"") -> bytes:
    """One ISO media box: a 32-bit size, a four-character type, then the payload."""
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def ebml_element(element_id: int, payload: bytes) -> bytes:
    """One EBML element with an 8-byte size field, as FFmpeg writes a WebM Segment."""
    size = (1 << 56) | len(payload)
    return element_id.to_bytes((element_id.bit_length() + 7) // 8, "big") + size.to_bytes(8, "big") + payload


def complete_clip(suffix: str) -> bytes:
    """The smallest MP4, MOV or WebM file that the worker's clip check accepts."""
    if suffix == ".webm":
        header = ebml_element(0x1A45DFA3, ebml_element(0x4282, b"webm"))
        return header + ebml_element(0x18538067, ebml_element(0x1F43B675, bytes(32)))
    return iso_box(b"ftyp", b"isom" + bytes(4)) + iso_box(b"free") + iso_box(b"mdat", bytes(64)) + iso_box(b"moov")


def write_clip(path: Path) -> Path:
    path.write_bytes(complete_clip(path.suffix.lower()))
    return path


_FAKE_MANIM = '''\
"""Write the groups of files plan.json lists, print its log and exit with its code.

seen.json records the run marker and what the media folder held when this
process started, the folder, arguments and environment it started with, and
the function behind each scene writer method that manim_main replaces. Like
Manim, it writes one file after another, so each file gets a later mtime than
the one before. After each group but the last, and after the last too in a
held plan, it writes held-<n>.txt and waits for go-<n>.txt. A plan with a
child first starts a process that sleeps with this one's stdout and stderr,
like a TeX child that outlives Manim, and writes its pid to child.txt. Like
Manim at the end of a render, it then marks each segment that a segment list
it wrote names as used, in list order, and in that list's folder keeps at most
max_files_cached segments, as the --config_file sets it or Manim's default of
100, deleting the least recently used; -1 keeps them all.
"""
import configparser
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from manim.scene.scene_file_writer import SceneFileWriter

ENVIRONMENT = ("HOME", "USERPROFILE", "shell_escape", "openin_any", "openout_any")
SEGMENTS = (".mov", ".mp4", ".webm")
here = Path(__file__).parent
plan = json.loads((here / "plan.json").read_text(encoding="utf-8"))
media = Path(sys.argv[sys.argv.index("--media_dir") + 1])
marker = media.parent / "manim-run.json"
seen = {
    "marker": json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None,
    "files": sorted(path.relative_to(media).as_posix() for path in media.rglob("*") if path.is_file()),
    "cwd": os.getcwd(),
    "argv": sys.argv[1:],
    "env": {name: os.environ.get(name) for name in ENVIRONMENT},
    "writer": {name: getattr(SceneFileWriter, name).__name__ for name in ("_concat_manifest_bytes", "write_frame")},
}
(here / "seen.json").write_text(json.dumps(seen), encoding="utf-8")
if plan["child"]:
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    child = subprocess.Popen(sleeper, stdout=sys.stdout, stderr=sys.stderr)
    (here / "child.txt").write_text(str(child.pid), encoding="utf-8")
groups = plan["groups"]
stamp = 0
listed = set()
for number, group in enumerate(groups):
    for name, data in group.items():
        target = Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes.fromhex(data))
        stamp = max(stamp + 1_000_000, time.time_ns())
        os.utime(target, ns=(stamp, stamp))
        if target.name == "partial_movie_file_list.txt":
            listed.add(target.parent)
    if number < len(groups) - 1 or plan["hold"]:
        (here / f"held-{number}.txt").write_text("", encoding="utf-8")
        while not (here / f"go-{number}.txt").exists():
            time.sleep(0.02)
settings = configparser.ConfigParser()
if "--config_file" in sys.argv:
    settings.read(sys.argv[sys.argv.index("--config_file") + 1], encoding="utf-8")
cached = settings.getint("CLI", "max_files_cached", fallback=100)
for folder in listed:
    for line in (folder / "partial_movie_file_list.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("file "):
            used = folder / line.rpartition("/")[2].removesuffix("'")
            stamp = max(stamp + 1_000_000, time.time_ns())
            os.utime(used, ns=(stamp, used.stat().st_mtime_ns))
    segments = sorted(
        (path for path in folder.iterdir() if path.suffix in SEGMENTS), key=lambda path: path.stat().st_atime
    )
    excess = len(segments) - cached if cached != -1 else 0
    for path in segments[: max(excess, 0)]:
        path.unlink()
for line in plan["log"]:
    print(line, flush=True)
sys.exit(plan["exit"])
'''


_FAKE_WRITER = """\
class SceneFileWriter:
    @staticmethod
    def _concat_manifest_bytes(input_files):
        raise NotImplementedError

    def write_frame(self, pixels, *, repeat=1):
        raise NotImplementedError
"""


class FakeManim:
    """A ``manim`` package on PYTHONPATH that stands in for Manim under ``mathimator_worker.manim_main``.

    Its scene writer has only the methods that ``manim_main`` replaces.
    """

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.package = root / "manim"
        (self.package / "scene").mkdir(parents=True)
        (self.package / "__init__.py").write_text("", encoding="utf-8")
        (self.package / "__main__.py").write_text(_FAKE_MANIM, encoding="utf-8")
        (self.package / "scene" / "__init__.py").write_text("", encoding="utf-8")
        (self.package / "scene" / "scene_file_writer.py").write_text(_FAKE_WRITER, encoding="utf-8")
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(root), str(WORKER)]))

    def plan(
        self,
        files: dict[Path, bytes],
        log: list[str] | None = None,
        exit_code: int = 0,
        *,
        hold: bool = False,
        later: list[dict[Path, bytes]] | None = None,
        child: bool = False,
    ) -> None:
        """Write ``files``, then each group of ``later`` once the hold before it is released.

        ``hold`` holds after the last group as well, before the log and the
        exit. ``child`` starts a sleeping child that shares the output pipes.
        """
        groups = [files, *(later or [])]
        payload = {
            "groups": [{str(path): data.hex() for path, data in group.items()} for group in groups],
            "log": log or [],
            "exit": exit_code,
            "hold": hold,
            "child": child,
        }
        (self.package / "plan.json").write_text(json.dumps(payload), encoding="utf-8")

    def child_pid(self) -> int:
        return int((self.package / "child.txt").read_text(encoding="utf-8"))

    def seen(self) -> dict[str, Any]:
        return json.loads((self.package / "seen.json").read_text(encoding="utf-8"))

    def ran(self) -> bool:
        return (self.package / "seen.json").exists()

    def wait_until_held(self, number: int = 0) -> None:
        deadline = time.monotonic() + 60
        while not (self.package / f"held-{number}.txt").exists():
            assert time.monotonic() < deadline, f"the fake manim never reached hold {number}"
            time.sleep(0.02)

    def release(self, number: int = 0) -> None:
        (self.package / f"go-{number}.txt").write_text("", encoding="utf-8")


def video_render(layout: ProjectLayout, quality: str, clips: list[bytes], suffix: str = ".mp4") -> dict[Path, bytes]:
    """The files of one finished video render: a partial per beat, the segment list and the movie.

    The segment list is written as ``manim_main`` makes Manim write it, quotes included.
    """
    folder = layout.partial_movie_dir(quality)
    files = {folder / f"{index:064x}{suffix}": data for index, data in enumerate(clips)}
    files[folder / "partial_movie_file_list.txt"] = concat_manifest_bytes([str(path) for path in files])
    files[folder.parent.parent / f"MainScene{suffix}"] = complete_clip(suffix)
    return files


def link_folder(link: Path, target: Path, kind: str = LINK_KINDS[0]) -> None:
    """A link to the folder ``target`` of one of the ``LINK_KINDS``.

    The default is the one any account can make: a junction on Windows, a
    symbolic link elsewhere. Windows makes a symbolic link only in Developer
    Mode or with the right to create one, so a test that asks for one there is
    skipped without it.
    """
    if sys.platform == "win32" and kind == "junction":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    _symlink(link, target, folder=True)


def link_file(link: Path, target: Path) -> None:
    """A symbolic link to the file ``target``, which Windows makes only as ``link_folder`` says."""
    _symlink(link, target, folder=False)


def _symlink(link: Path, target: Path, *, folder: bool) -> None:
    """A symbolic link to ``target``, or a skipped test on a Windows account that cannot make one."""
    try:
        link.symlink_to(target, target_is_directory=folder)
    except OSError as exc:
        if sys.platform != "win32":
            raise
        pytest.skip(f"this account cannot make symbolic links: {exc}")


@contextlib.contextmanager
def unreadable(path: Path) -> Iterator[None]:
    """Keep ``path`` unreadable: open with no sharing on Windows, mode 000 elsewhere."""
    if sys.platform == "win32":
        import _winapi

        generic_read, open_existing = 0x80000000, 3
        handle = _winapi.CreateFile(str(path), generic_read, 0, 0, open_existing, 0, 0)
        try:
            yield
        finally:
            _winapi.CloseHandle(handle)
        return
    if os.geteuid() == 0:
        pytest.skip("root reads files whatever their mode")
    path.chmod(0)
    try:
        yield
    finally:
        path.chmod(0o644)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path) -> tuple[float, int, int]:
    """Return duration in seconds, width, and height. Imports PyAV in the test process."""
    import av

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.duration is not None and stream.time_base is not None:
            duration = float(stream.duration * stream.time_base)
        elif container.duration is not None and container.duration > 0:
            # PyAV 16 exposes av.time_base as the integer AV_TIME_BASE (microseconds).
            base = av.time_base
            if isinstance(base, int):
                duration = float(container.duration) / float(base)
            else:
                duration = float(container.duration * base)
        else:
            raise RuntimeError(f"no duration metadata in {path}")
        width = int(stream.codec_context.width)
        height = int(stream.codec_context.height)
    return duration, width, height


def frame_count(path: Path) -> int:
    """The frames of the video in ``path``, each decoded. Imports PyAV in the test process."""
    import av

    with av.open(str(path)) as container:
        return sum(1 for _ in container.decode(video=0))


class Stdout:
    """Every line an in-process supervisor wrote, each parsed as strict JSON, the way the shell parses it."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self._capsys = capsys
        self.lines: list[dict[str, Any]] = []

    def read(self) -> list[dict[str, Any]]:
        for text in self._capsys.readouterr().out.splitlines():
            line = loads(text)
            assert isinstance(line, dict), text
            self.lines.append(line)
        return self.lines

    def last(self) -> dict[str, Any]:
        return self.read()[-1]

    def of(self, job_id: str | None) -> list[dict[str, Any]]:
        return [line for line in self.read() if line["jobId"] == job_id]

    def wait_for(self, job_id: str, event: str) -> dict[str, Any]:
        """The job's first ``event`` line. Fails at once when the job ended without one (C7.1)."""
        deadline = time.monotonic() + 30
        while True:
            lines = self.of(job_id)
            found = [line for line in lines if line["event"] == event]
            if found:
                return found[0]
            assert not any(line["event"] in TERMINAL_EVENTS for line in lines), lines
            assert time.monotonic() < deadline, self.lines
            time.sleep(0.02)


class SupervisorClient:
    """A supervisor in its own process, spoken to over stdin and stdout as the shell speaks to it.

    The last lines of its stderr are kept, so a wait that fails shows the
    traceback of a supervisor that died. A wait fails as soon as stdout closes.
    """

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.events: list[dict[str, object]] = []
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_LINES)
        self._closed = False
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._read, name="supervisor-stdout", daemon=True)
        self._thread.start()
        self._stderr = threading.Thread(target=self._drain_stderr, name="supervisor-stderr", daemon=True)
        self._stderr.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                event = {"event": "error", "jobId": None, "message": f"non-json stdout: {text}"}
            if not isinstance(event, dict):
                event = {"event": "error", "jobId": None, "message": f"non-object stdout: {text}"}
            with self._condition:
                self.events.append(event)
                self._condition.notify_all()
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _drain_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        for raw in self.proc.stderr:
            self._stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())

    def send(self, message: dict[str, object]) -> None:
        assert self.proc.stdin is not None
        payload = (json.dumps(message) + "\n").encode("utf-8")
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def wait_for(self, predicate: Callable[[dict[str, object]], bool], timeout: float) -> dict[str, object]:
        """The first event that matches. Raises once stdout has closed without one, or at the timeout."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event in self.events:
                    if predicate(event):
                        return event
                if self._closed:
                    raise AssertionError(self._failure("the supervisor closed its stdout"))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(self._failure(f"no matching event within {timeout} s"))
                self._condition.wait(timeout=remaining)

    def wait_for_end(self, job_id: str, timeout: float) -> dict[str, object]:
        """The job's one terminal line: ``done``, ``error`` or ``rejected`` (C7.1)."""
        return self.wait_for(
            lambda event: event.get("jobId") == job_id and event.get("event") in TERMINAL_EVENTS, timeout
        )

    def _failure(self, reason: str) -> str:
        """``reason``, with the exit code, the last events and the last lines of stderr."""
        if self._closed:
            # The process is ending: give it time to exit and its last stderr lines time to arrive.
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)
            self._stderr.join(timeout=5)
        stderr = "\n".join(self._stderr_tail)
        events = json.dumps(self.events[-12:], indent=2)
        return f"{reason}; exit code {self.proc.poll()}\nlast events: {events}\nstderr:\n{stderr}"

    def close(self) -> None:
        if self.proc.poll() is None and self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def scene_path(project: Path) -> Path:
    return ProjectLayout(project).scene_path


def media_dir(project: Path) -> Path:
    return ProjectLayout(project).media_dir


def handwritten_project(root: Path, scene_file: Path) -> Path:
    """A project directory whose scene file is copied in and not generated."""
    if root.exists():
        shutil.rmtree(root)
    destination = scene_path(root)
    destination.parent.mkdir(parents=True)
    shutil.copyfile(scene_file, destination)
    return root


def fixture_project(root: Path, fixture: Path) -> Path:
    """A new project directory holding a copy of the project.json ``fixture``."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    shutil.copyfile(fixture, root / "project.json")
    return root


def start_supervisor(
    project: Path,
    log_path: Path,
    *,
    python: Path | None = None,
    init: bool = False,
    supervisor_python: Path | None = None,
    pythonpath: list[Path] | None = None,
) -> SupervisorClient:
    """Start a supervisor for ``project``. ``pythonpath`` goes before the worker on PYTHONPATH, for it and its Manim.

    The supervisor needs only the standard library; Manim is needed by the render tests alone.
    """
    interpreter = supervisor_python or venv_python()
    if not interpreter.is_file():
        raise FileNotFoundError(
            f"no interpreter at {interpreter}. Set MATHIMATOR_PYTHON to any Python 3.13 or later, "
            "or run `uv sync --locked` from the repo root to create .venv. Only the render tests need Manim."
        )
    manim_python = python or interpreter
    project.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([*map(str, pythonpath or []), str(WORKER)])
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    command = [
        str(interpreter),
        "-m",
        "mathimator_worker",
        "--project",
        str(project),
        "--python",
        str(manim_python),
        "--log",
        str(log_path),
    ]
    if init:
        command.append("--init")
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return SupervisorClient(proc)


def read_argv(log_path: Path, timeout: float = 60) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.is_file():
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                marker = "argv: "
                if marker in line:
                    return json.loads(line.split(marker, 1)[1])
        time.sleep(0.05)
    raise TimeoutError(f"argv was not logged in {log_path}")


def manim_pids(log_path: Path) -> list[int]:
    """The pid of each Manim the supervisor's log says it started, in order."""
    if not log_path.is_file():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [int(line.rsplit("pid=", 1)[1]) for line in lines if "manim started pid=" in line]


def assert_phase1_argv(argv: list[str], *, width: int, height: int, fps: int) -> None:
    assert "--renderer" in argv
    assert argv[argv.index("--renderer") + 1] == "cairo"
    assert "--seed" in argv
    assert argv[argv.index("--seed") + 1] == "1"
    assert "-r" in argv
    assert argv[argv.index("-r") + 1] == f"{width},{height}"
    assert "--fps" in argv
    assert argv[argv.index("--fps") + 1] == str(fps)
    assert "--format" in argv
    assert argv[argv.index("--format") + 1] == "mp4"
    assert "--max-inflight-encoders" in argv
    assert argv[argv.index("--max-inflight-encoders") + 1] == "1"
    joined = " ".join(argv)
    assert "opengl" not in joined
    assert "--live-preview" not in joined
    assert "--preview" not in joined
    assert "--disable_caching" not in joined
    assert "--flush_cache" not in joined
    assert "--from_animation_number" not in joined
