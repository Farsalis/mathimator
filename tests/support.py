"""Helpers for the phase 1 supervisor tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "worker"
SCENES = REPO / "scenes"
OUT = REPO / "out" / "phase1"
REFERENCE = SCENES / "reference_60.py"
LAST_BEAT = SCENES / "reference_60_last_beat.py"
RUNTIME_ERROR = SCENES / "runtime_error.py"


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
    fake interpreter there is a small compiled program.
    """
    if os.name != "nt":
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            f"time.sleep({sleep_s})\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path
    exe = path.with_suffix(".exe")
    source = path.with_suffix(".cs")
    millis = max(1, int(sleep_s * 1000))
    source.write_text(
        "class Sleeper { static void Main() { System.Threading.Thread.Sleep("
        f"{millis}); }} }}\n",
        encoding="utf-8",
    )
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


class SupervisorClient:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.events: list[dict[str, object]] = []
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

    def _drain_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        for _line in self.proc.stderr:
            continue

    def send(self, message: dict[str, object]) -> None:
        assert self.proc.stdin is not None
        payload = (json.dumps(message) + "\n").encode("utf-8")
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event in self.events:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(json.dumps(self.events[-12:], indent=2))
                self._condition.wait(timeout=remaining)

    def close(self) -> None:
        if self.proc.poll() is None and self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def scene_path(project: Path) -> Path:
    return project / ".mathimator" / "codegen" / "scene.py"


def media_dir(project: Path) -> Path:
    return project / ".mathimator" / "cache" / "media"


def handwritten_project(root: Path, scene_file: Path) -> Path:
    """A project directory whose scene file is copied in and not generated."""
    if root.exists():
        shutil.rmtree(root)
    destination = scene_path(root)
    destination.parent.mkdir(parents=True)
    shutil.copyfile(scene_file, destination)
    return root


def start_supervisor(
    project: Path,
    log_path: Path,
    *,
    python: Path | None = None,
    init: bool = False,
    supervisor_python: Path | None = None,
) -> SupervisorClient:
    interpreter = supervisor_python or venv_python()
    if not interpreter.is_file():
        raise FileNotFoundError(
            f"render interpreter not found at {interpreter}. Create .venv and install manim==0.22.0."
        )
    manim_python = python or interpreter
    project.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKER)
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
