"""Long-lived stdio supervisor. One Manim grandchild at a time.

It speaks protocol 2 (``protocol.py``). The commands are ``status``,
``get_state``, ``edit``, ``undo``, ``redo``, ``code``, ``clear_cache``,
``open_project``, ``reload``, ``overwrite``, ``save_as``, ``render_preview``,
``export`` and ``cancel``. Every request gets exactly one reply line, except
the jobs and ``cancel``: a job sends ``queued`` when it has to wait,
``started``, ``beat_ready`` (previews only) and one terminal ``done``,
``error`` or ``rejected`` (C7.1). An unexpected
exception is answered with ``error internal`` and the worker keeps serving;
every state change is all-or-nothing, so nothing is left half changed (D03).

The worker is bound to at most one project folder (contract C8). While bound
it holds the folder's lock (D58), the folder's identity (D57) and the SHA-256
of project.json as last read or written (D59). A write or a render first
checks that the folder is still there and that project.json is unchanged on
disk, and refuses otherwise. A render then refuses a scene that cannot be
generated, a scene with formulas while TeX is unavailable, and a project path
Manim cannot use (C7.2). Startup never exits on a project problem: the
worker stays unbound and says why in ``ready.startupError``. The worker never
creates a project folder and never writes an empty project.json over a missing
one.

This process does not import Manim or PyAV, and at most one Manim runs. A new
preview cancels an in-flight preview, and an export cancels an in-flight
preview, then each starts in its place (C7.2). A request whose predecessor is
still alive 1 s after it was told to stop waits in a hold slot, one for an
export and one for a preview, and gets ``queued`` (D19). While an export runs
or waits, the newest preview request waits behind it. Document edits never
cancel an export and never start one. Progress comes from the files Manim
writes, not from its log (``progress.py``, D13), and a finished export is
copied into ``exports/`` outside the document lock (F164).

A folder without project.json, started without ``--init``, is in hand-written
mode (D42): there is no document, and jobs render the scene.py found in
``.mathimator/codegen/``. Only the phase 1 tests use it.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from mathimator_worker.argv import build_manim_argv, manim_environment, manim_interpreter, profile_size
from mathimator_worker.clip_check import SEGMENT_SUFFIXES, ClipChecker
from mathimator_worker.codegen import CodegenError, generate, needs_latex
from mathimator_worker.document import ProfileName, Project, dumps_project, empty_project, load_bytes, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.exports import (
    PublishFailed,
    existing_export,
    find_export_output,
    frame_count_problem,
    publish_export,
)
from mathimator_worker.identity import beat_chain_keys, export_key
from mathimator_worker.jsonio import dumps_record, read_record
from mathimator_worker.layout import ProjectLayout
from mathimator_worker.manim_paths import manim_root
from mathimator_worker.preview_index import PreviewIndex, load_index, rebuild_index
from mathimator_worker.process_group import terminate_process_group, warm_process_table
from mathimator_worker.progress import ManimProgress, discard_unfinished_partials, file_stamps, written_since
from mathimator_worker.project_folder import (
    ProjectLock,
    ProjectLocked,
    fingerprint,
    folder_identity,
    make_folders,
    os_reason,
    remove_file,
    remove_quietly,
    write_atomically,
)
from mathimator_worker.protocol import (
    PROTOCOL_VERSION,
    BadRequest,
    Request,
    cancelled,
    error,
    internal,
    parse_request,
    queued,
    rejected,
)
from mathimator_worker.session import EditResult, Session
from mathimator_worker.tex_failure import failed_tex_log, format_render_failure
from mathimator_worker.win32_job import assign_kill_on_close, close_job, jobs_enabled, terminate_job

NOT_A_PROJECT = "That folder is not a Mathimator project."
PROJECT_UNAVAILABLE = "The project folder was moved or deleted."
PROJECT_LOCKED = "This project is open in another Mathimator window."
PROJECT_CHANGED = "project.json was changed outside Mathimator."
EXPORT_RUNNING = "An export is already running."
FILES_IN_USE = (
    "Some cache files are in use and were not removed. Close other programs using this project and try again."
)
# worker.log moves to worker.log.1 at this size, replacing the older copy (D55).
LOG_LIMIT = 5 * 1024 * 1024
# Events whose payload the log records as a size only (D55).
_SUMMARIZED_EVENTS = frozenset({"state", "code"})
# How often a running preview looks for finished partials.
_POLL_SECONDS = 0.1
# How long a cancelled job waits for Manim's pipes to close, which a child that escaped the kill can hold open (F036).
_DRAIN_SECONDS = 0.5
# The output lines a failure keeps as its details (D23).
_TAIL_LINES = 80


@dataclass
class _Job:
    job_id: str
    profile: ProfileName
    thread: threading.Thread
    cancel: threading.Event
    # An export that skips the export cache (D56).
    force: bool = False
    proc: subprocess.Popen[str] | None = None
    win_job: int | None = None
    # The folder the job started in. It renders and publishes there, whatever is bound later.
    layout: ProjectLayout | None = None
    # The scene's folder, which Manim runs in and takes as its home, through the project's 8.3 alias if TeX needs it.
    manim_dir: Path | None = None
    argv: list[str] = field(default_factory=list)
    quality: str = ""
    # The binding generation the job started under (C7.1).
    generation: int = 0
    captured: Project | None = None
    # A project-mode preview: the chain keys of the document it renders (C7.1, F135).
    capture_keys: list[str] | None = None
    # Where its progress starts: the beats whose partial is current, and the beats Manim renders (C7.2).
    ready: dict[int, Path] = field(default_factory=dict)
    waiting: list[int] | None = None
    # Set with the job's one terminal line (done, error or rejected).
    ended: bool = False
    # When the job was first told to stop, by a cancel or a newer job, on the monotonic clock (D19).
    stop_requested: float | None = None

    def tell_to_stop(self) -> float:
        """Set ``cancel`` and return when the job was first told to stop."""
        if self.stop_requested is None:
            self.stop_requested = time.monotonic()
        self.cancel.set()
        return self.stop_requested

    @property
    def output_format(self) -> str:
        if self.profile == "export" and self.captured is not None:
            return self.captured.export_profile.format
        return "mp4"

    @property
    def video_dir(self) -> Path:
        """The quality folder Manim writes this job's partials and movie into."""
        assert self.layout is not None
        return self.layout.video_dir(self.quality)


@dataclass
class _Binding:
    """What the worker holds for its bound folder (C8.1)."""

    lock: ProjectLock
    # The folder's (st_dev, st_ino) when it was bound (D57).
    identity: tuple[int, int]
    # The SHA-256 of project.json as last read or written (D59).
    fingerprint: str


@dataclass
class _Claim:
    """A folder that is locked and read, ready to bind. ``undo`` removes what claiming it created."""

    folder: Path
    binding: _Binding
    project: Project
    undo: Callable[[], None]


class Supervisor:
    def __init__(
        self,
        project: Path,
        python: str,
        log_path: Path | None,
        init: bool,
    ) -> None:
        self.python = python
        # What runs Manim: the real interpreter behind a venv launcher (D16).
        self._manim_python, self._venv_launcher = manim_interpreter(python)
        # Resolve like _open_project does. Windows may hand over an 8.3 alias
        # such as C:\Users\JOHNSM~1, and TeX stops reading a path at "~" (G4.1).
        # One attribute, so a bind replaces the folder and all its paths at once.
        self.layout = ProjectLayout(project.resolve())
        self._log_override = log_path
        self._log_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._doc_lock = threading.Lock()
        self._stdout_lock = threading.Lock()
        # The job whose thread runs, and the jobs held until it ends (C7.2). A held job implies a current one.
        self._current: _Job | None = None
        self._held_export: _Job | None = None
        self._held_preview: _Job | None = None
        self.session: Session | None = None
        self._index: PreviewIndex | None = None
        self._binding: _Binding | None = None
        # Counts the binds. A job records the one it started under (C7.1).
        self._generation = 0
        # Why there is no session: the refusal of the startup project.json, or not_a_project in hand-written mode.
        self._startup_error: DocumentError | None = None
        self._handwritten = False
        # Why formulas cannot render, when the shell found TeX missing or failing its self-test (D31, C14 Q4).
        self._tex_error = os.environ.get("MATHIMATOR_TEX_ERROR") or None
        self._clips = ClipChecker()
        # The commands of protocol.REQUEST_FIELDS, each answered from its parsed request.
        self._commands: dict[str, Callable[[Request], None]] = {
            "status": self._status,
            "get_state": self._get_state,
            "edit": self._edit,
            "undo": self._undo,
            "redo": self._redo,
            "code": self._code,
            "clear_cache": self._clear_cache,
            "open_project": self._open_project,
            "reload": self._reload,
            "overwrite": self._overwrite,
            "save_as": self._save_as,
            "render_preview": self._render_preview,
            "export": self._export,
            "cancel": self._cancel,
        }
        self._start(init)

    @property
    def project(self) -> Path:
        """The folder the worker is bound to, or would bind at startup."""
        return self.layout.root

    @property
    def log_path(self) -> Path:
        """The ``--log`` file, or else the bound folder's worker.log."""
        return self._log_override if self._log_override is not None else self.layout.log_path

    def _start(self, init: bool) -> None:
        """Bind the startup folder in the order of C8.2. A refusal leaves the worker unbound and serving."""
        identity = folder_identity(self.project)
        if identity is None:
            # Nothing is created, and the log goes to stderr (G1.3).
            self._startup_error = DocumentError(PROJECT_UNAVAILABLE, code="project_unavailable")
            return
        if not init and not self.layout.project_file.exists():
            self._handwritten = True
            self._startup_error = DocumentError(NOT_A_PROJECT, code="not_a_project")
            self._sweep_interrupted_run()
            return
        try:
            claim = self._claim(self.project, identity, init=init)
        except DocumentError as exc:
            self._startup_error = exc
            self.log(f"project refused: {exc}")
            return
        with self._doc_lock:
            self._bind_claim_locked(claim)

    def close(self) -> None:
        """Stop the running job, drop the held ones and release the project lock, as exiting does.

        The running job gets 1 s to end, so a Manim that starts while the
        worker closes is still killed by its job, not left running (F028).
        """
        with self._state_lock:
            current, self._held_export, self._held_preview = self._current, None, None
        if current is not None:
            self._stop(current)
        with self._doc_lock:
            if self._binding is not None:
                self._binding.lock.release()

    def _sweep_interrupted_run(self) -> None:
        """Remove what a Manim run cut off by a quit, a crash or a kill left behind (G1.1)."""
        started = _marker_start(self.layout.run_marker)
        if started is None:
            return
        self.log(f"sweeping media from an interrupted run started={started}")
        try:
            removed = discard_unfinished_partials(self.layout.media_dir, started, set())
        except OSError as exc:
            # The marker stays, so the next bind sweeps again.
            self.log(f"could not sweep {self.layout.media_dir}: {exc}")
            return
        for path in removed:
            self.log(f"removed unfinished media {path}")
        self._remove_run_marker(self.layout.run_marker)

    def _warm_process_control(self) -> None:
        try:
            warm_process_table()
        except OSError as exc:
            self.log(f"process table warmup failed: {exc}")

    def log(self, message: str) -> None:
        """Append one line to the log. Never raises (D03) and never creates the project folder (G1.3).

        A line goes to stderr when the log file cannot take it, or when no
        folder is bound and there is no ``--log`` file.
        """
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        line = f"{stamp} {message}\n"
        with self._log_lock:
            if self._append_log_locked(line):
                return
            with contextlib.suppress(OSError, ValueError):
                sys.stderr.write(line)
                sys.stderr.flush()

    def _append_log_locked(self, line: str) -> bool:
        """Append ``line`` to the log file. Return False when there is none or it cannot take the line."""
        # Read once: log_path comes from the layout, which a bind on another thread may swap.
        log_path = self.log_path
        if self._log_override is None:
            if self._binding is None and not self._handwritten:
                return False
            try:
                # The log is <project>/.mathimator/logs/worker.log, so this checks the folder it is written to.
                make_folders(log_path.parents[2], log_path.parent)
            except OSError:
                return False
        _rotate_log(log_path)
        try:
            # A lone surrogate from a refused document must not stop the log.
            with log_path.open("a", encoding="utf-8", errors="backslashreplace") as handle:
                handle.write(line)
        except OSError:
            return False
        return True

    def _log_exception(self, context: str) -> None:
        """Log ``context`` with the traceback of the exception being handled."""
        self.log(f"{context}\n{traceback.format_exc().rstrip()}")

    def emit(self, event: dict[str, object]) -> None:
        """Write one line to stdout.

        A line JSON cannot hold, such as one with a NaN, goes out as ``error internal``
        for the same id instead, so the shell never reads a line it cannot parse (C2).
        The log records ``state`` and ``code`` lines by size only (D55).
        """
        try:
            payload = json.dumps(event, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            self._log_exception(f"could not serialize a {event.get('event')} line")
            job_id = event.get("jobId")
            event = internal(job_id if isinstance(job_id, str) else None, exc)
            payload = json.dumps(event, ensure_ascii=False)
        with self._stdout_lock:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
        name = event.get("event")
        if name in _SUMMARIZED_EVENTS:
            self.log(f"event {name} jobId={event.get('jobId')} bytes={len(payload.encode('utf-8'))}")
        else:
            self.log(f"event {payload}")

    def serve(self) -> None:
        latex, dvisvgm = shutil.which("latex"), shutil.which("dvisvgm")
        self.log(f"supervisor listening project={self.project} python={self.python} latex={latex} dvisvgm={dvisvgm}")
        if self._tex_error is not None:
            self.log(f"formulas cannot render: {self._tex_error}")
        try:
            for line in sys.stdin:
                self.handle_line(line)
        finally:
            self.close()

    def handle_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        self.log(f"command {text}")
        try:
            request = parse_request(text)
        except BadRequest as exc:
            self._emit_error(exc.job_id, "bad_request", str(exc))
            return
        try:
            self._commands[request.cmd](request)
        except Exception as exc:
            # D03: answer at once and keep serving, with the undo history and any
            # running export. Every state change is all-or-nothing, so none is half done.
            self._log_exception(f"{request.cmd} {request.id} failed")
            self.emit(internal(request.id, exc))

    def _reject(self, job_id: str, code: str, message: str, field: str | None = None) -> None:
        self.emit(rejected(job_id, code, message, field))

    def _emit_error(self, job_id: str | None, code: str, message: str, details: str | None = None) -> None:
        self.emit(error(job_id, code, message, details))

    def _claim(self, folder: Path, identity: tuple[int, int], *, init: bool) -> _Claim:
        """Lock ``folder``, write an empty project.json there when ``init`` asks for one, and read it.

        These are steps 3 to 5 of C8.2 and 4 to 6 of C8.3. Reopening the bound
        folder keeps its lock. A refusal raises DocumentError after undoing
        what the attempt created.
        """
        project_file = ProjectLayout(folder).project_file
        with contextlib.ExitStack() as undo:
            bound = self._binding
            if bound is not None and bound.identity == identity:
                lock = bound.lock
            else:
                lock = _lock_folder(folder)
                undo.callback(lock.discard)
            if init and not project_file.exists():
                try:
                    write_atomically(project_file, dumps_project(empty_project()).encode("utf-8"))
                except OSError as exc:
                    raise _save_failed(exc) from exc
                undo.callback(remove_quietly, project_file)
            data = _read_project_bytes(project_file)
            project = load_bytes(data)
            return _Claim(folder, _Binding(lock, identity, fingerprint(data)), project, undo.pop_all().close)

    def _bind_claim_locked(self, claim: _Claim) -> None:
        """Bind a claimed folder with a new session, undo history cleared. Caller holds ``_doc_lock``."""
        try:
            self._bind_locked(claim.folder, claim.binding, Session(claim.project))
        except BaseException:
            claim.undo()
            raise

    def _bind_locked(self, folder: Path, binding: _Binding, session: Session) -> None:
        """Bind ``folder`` with ``session`` under the next binding generation. Caller holds ``_doc_lock``.

        A new session takes the folder's render records; the bound session
        (Save As) keeps its own. The preview index is read from the folder
        and rebuilt. A bind that fails part way puts the old binding back
        whole, with its lock still held (D03, F005).
        """
        previous, old_session = self._binding, self.session
        kept = (self.layout, previous, old_session, self._index, self._handwritten, self._startup_error)
        try:
            self.layout, self._binding, self.session = ProjectLayout(folder), binding, session
            self._handwritten = False
            self._startup_error = None
            if not self._render_running_in(self.layout):
                self._sweep_interrupted_run()
            if session is not old_session:
                session.load_rendered(self._read_rendered())
            self._index = self._read_index()
            self._rebuild_index_locked()
            self._save_sidecars_locked()
        except BaseException:
            self.layout, self._binding, self.session, self._index, self._handwritten, self._startup_error = kept
            raise
        self._generation += 1
        if previous is not None and previous.lock is not binding.lock:
            previous.lock.release()

    def _status(self, request: Request) -> None:
        startup = self._startup_error
        self.emit(
            {
                "event": "ready",
                "jobId": request.id,
                "protocol": PROTOCOL_VERSION,
                "startupError": None if startup is None else {"code": startup.code, "message": str(startup)},
            }
        )

    def _get_state(self, request: Request) -> None:
        with self._doc_lock:
            event = self._state_locked(request.id)
        self.emit(event)

    def _state_locked(self, job_id: str) -> dict[str, object]:
        """The ``state`` reply, or the ``no_project`` refusal when there is no document."""
        if self.session is None:
            return self._no_project(job_id)
        return self.session.state_event(job_id, str(self.project), self._index)

    def _no_project(self, job_id: str) -> dict[str, object]:
        """The refusal of a request that needs a document while none is open."""
        reason = "" if self._startup_error is None else f" {self._startup_error}"
        return rejected(job_id, "no_project", f"No project is open.{reason}")

    def _edit(self, request: Request) -> None:
        """Apply one edit in the order of C8.1. An edit that changes nothing writes nothing (D08)."""
        with self._doc_lock:
            session = self.session
            if session is None:
                event = self._no_project(request.id)
            else:
                try:
                    project, result = session.edited(request.mapping("edit"))
                    payload = dumps_project(project)
                    changed = payload != dumps_project(session.project)
                    if changed:
                        # Saved before it is committed, so a refused or failed save changes nothing (D05).
                        self._save_locked(payload)
                except DocumentError as exc:
                    event = _refusal(request.id, exc)
                else:
                    if changed:
                        session.commit(project)
                        event = self._changed_locked(request.id)
                    else:
                        # No undo step, and Redo is kept.
                        event = self._state_locked(request.id)
                    event["changed"] = changed
                    self._attach_focus(event, result)
        self.emit(event)

    def _undo(self, request: Request) -> None:
        self._history(request.id, undo=True)

    def _redo(self, request: Request) -> None:
        self._history(request.id, undo=False)

    def _history(self, job_id: str, *, undo: bool) -> None:
        with self._doc_lock:
            session = self.session
            if session is None:
                event = self._no_project(job_id)
            else:
                try:
                    project = session.undo_target() if undo else session.redo_target()
                    self._save_locked(dumps_project(project))
                except DocumentError as exc:
                    event = _refusal(job_id, exc)
                else:
                    if undo:
                        session.undo()
                    else:
                        session.redo()
                    event = self._changed_locked(job_id)
        self.emit(event)

    def _changed_locked(self, job_id: str) -> dict[str, object]:
        """Bring the index and the cache records up to a committed change, and return the ``state`` reply."""
        self._rebuild_index_locked()
        self._save_sidecars_locked()
        return self._state_locked(job_id)

    def _code(self, request: Request) -> None:
        with self._doc_lock:
            session = self.session
            if session is None:
                event = self._no_project(request.id)
            else:
                try:
                    event = {"event": "code", "jobId": request.id, "source": generate(session.project)}
                except CodegenError as exc:
                    event = rejected(request.id, "invalid_document", str(exc))
        self.emit(event)

    def _open_project(self, request: Request) -> None:
        """Bind a folder, or the bound one afresh, in the order of C8.3. A refusal changes nothing."""
        if self._export_running():
            self._reject(request.id, "export_running", EXPORT_RUNNING)
            return
        raw_path = request.string("path")
        folder = Path(raw_path)
        if folder.name == "project.json" and folder.is_file():
            folder = folder.parent
        init = request.flag("init")
        # An empty path names no folder, although Path("") is the working directory.
        identity = folder_identity(folder) if raw_path else None
        if identity is None or not (init or ProjectLayout(folder).project_file.exists()):
            self._reject(request.id, "not_a_project", NOT_A_PROJECT)
            return
        try:
            claim = self._claim(folder.resolve(), identity, init=init)
        except DocumentError as exc:
            self.emit(_refusal(request.id, exc))
            return
        self._stop_previews()
        with self._doc_lock:
            self._bind_claim_locked(claim)
            event = self._state_locked(request.id)
        self.emit(event)

    def _reload(self, request: Request) -> None:
        """Read project.json again as a new session, undo history cleared (C8.3). Jobs keep running."""
        with self._doc_lock:
            session = self.session
            if session is None:
                event = self._no_project(request.id)
            else:
                try:
                    binding, data = self._read_disk_locked()
                    project = load_bytes(data)
                except DocumentError as exc:
                    event = _refusal(request.id, exc)
                else:
                    replacement = Session(project)
                    replacement.rendered = session.rendered
                    self.session = replacement
                    binding.fingerprint = fingerprint(data)
                    event = self._changed_locked(request.id)
        self.emit(event)

    def _overwrite(self, request: Request) -> None:
        """Write the document in memory over project.json, whatever changed on disk (D59)."""
        with self._doc_lock:
            session = self.session
            if session is None:
                event = self._no_project(request.id)
            else:
                try:
                    self._save_locked(dumps_project(session.project), overwrite=True)
                except DocumentError as exc:
                    event = _refusal(request.id, exc)
                else:
                    event = self._state_locked(request.id)
        self.emit(event)

    def _save_as(self, request: Request) -> None:
        """Copy the project into an empty folder and bind that, keeping the session and its history (D29, C8.3).

        The old folder is never changed. A refusal removes what was written in the target.
        """
        session = self.session
        if session is None:
            self.emit(self._no_project(request.id))
            return
        if self._export_running():
            self._reject(request.id, "export_running", EXPORT_RUNNING)
            return
        raw_path = request.string("path")
        target = Path(raw_path)
        identity = folder_identity(target) if raw_path else None
        if identity is None:
            self._reject(request.id, "save_as_failed", _save_as_failed(target, "it is not a folder"))
            return
        problem = self._save_as_problem(target, identity)
        if problem is not None:
            self._reject(request.id, "save_as_failed", _save_as_failed(target, problem))
            return
        target = target.resolve()
        data = dumps_project(session.project).encode("utf-8")
        try:
            with contextlib.ExitStack() as undo:
                lock = ProjectLock.acquire(target)
                undo.callback(lock.discard)
                self._stop_previews()
                project_file = ProjectLayout(target).project_file
                write_atomically(project_file, data)
                undo.callback(remove_quietly, project_file)
                self._copy_cache_to(target, session)
                rollback = undo.pop_all()
        except OSError as exc:
            self._reject(request.id, "save_as_failed", _save_as_failed(target, os_reason(exc)))
            return
        with self._doc_lock:
            try:
                self._bind_locked(target, _Binding(lock, identity, fingerprint(data)), session)
            except BaseException:
                rollback.close()
                raise
            event = self._state_locked(request.id)
        self.emit(event)

    def _save_as_problem(self, target: Path, identity: tuple[int, int]) -> str | None:
        """Why the folder ``target`` cannot take the project, or None when it can."""
        if self._binding is not None and identity == self._binding.identity:
            return "it is the folder of the open project"
        for name in ("project.json", ".mathimator"):
            if (target / name).exists():
                return f"it already holds {name}"
        return None

    def _copy_cache_to(self, target: Path, session: Session) -> None:
        """Copy the render cache and the exports into ``target`` (C8.3).

        A folder the old project no longer has is skipped (D57). The cache
        records are written from memory, with the index paths relative to the
        project, so they name the copies. Nothing else is copied: not the
        generated code, the glyph files, the logs, the lock or a temp-project
        marker.
        """
        layout = self.layout
        for source in (layout.media_dir, layout.last_good_dir, layout.exports_dir):
            if not source.is_dir():
                continue
            try:
                shutil.copytree(source, target / source.relative_to(layout.root))
            except shutil.Error as exc:
                # copytree reports each file it could not copy as text. The first one says why.
                raise OSError(exc.args[0][0][2]) from exc
        with self._doc_lock:
            records: dict[Path, object] = {layout.rendered_path: session.rendered_payload()}
            if self._index is not None:
                records[layout.index_path] = self._index.stored(layout.root)
        for path, value in records.items():
            copy = target / path.relative_to(layout.root)
            make_folders(target, copy.parent)
            copy.write_text(dumps_record(value), encoding="utf-8", newline="\n")

    def _export_running(self) -> bool:
        with self._state_lock:
            return self._export_running_locked()

    def _export_running_locked(self) -> bool:
        """Whether an export runs or is held (D22). Caller holds ``_state_lock``."""
        current = self._current
        return self._held_export is not None or (current is not None and current.profile == "export")

    def _attach_focus(self, payload: dict[str, object], result: EditResult) -> None:
        if result.focus_beat_id is None and result.focus_object_id is None:
            return
        payload["focus"] = {
            "beatId": result.focus_beat_id,
            "objectId": result.focus_object_id,
        }

    def _render_preview(self, request: Request) -> None:
        """Start a preview, or hold it while the render before it runs or stops (C7.2)."""
        job = self._new_job(request.id, "preview")
        with self._state_lock:
            current = self._current
            if current is None:
                self._launch_locked(job)
                return
            replaced, self._held_preview = self._held_preview, job
            behind_export = self._export_running_locked()
        if replaced is not None:
            # The newer preview takes the slot; the one it replaces is cancelled (C7.3).
            self.emit(cancelled(replaced.job_id))
        if behind_export:
            self.log(f"preview {job.job_id} waiting for export")
        else:
            self._supersede(current, job)

    def _export(self, request: Request) -> None:
        """Start an export, or hold it while the preview before it stops (C7.2). One export runs or waits at a time."""
        job = self._new_job(request.id, "export", force=request.flag("force"))
        with self._state_lock:
            current = self._current
            running = self._export_running_locked()
            if current is None:
                self._launch_locked(job)
            elif not running:
                self._held_export = job
        if running:
            self._reject(job.job_id, "export_running", EXPORT_RUNNING)
        elif current is not None:
            self._supersede(current, job)

    def _supersede(self, current: _Job, job: _Job) -> None:
        """Stop the preview ``current`` for the held ``job``, which starts when ``current`` has ended (C7.2).

        A preview still alive 1 s after it was first told to stop keeps
        ``job`` waiting in its slot, and ``queued`` says so (D19, F036).
        """
        if self._stop(current):
            return
        with self._state_lock:
            # Under the lock that starts a held job, so queued always comes before started.
            if job is self._held_export or job is self._held_preview:
                self.emit(queued(job.job_id))

    def _stop_previews(self) -> None:
        """Before a switch: cancel a held preview and stop a running one (C8.3).

        The switch goes ahead even if the running preview is still alive after 1 s.
        """
        with self._state_lock:
            current = self._current
            held, self._held_preview = self._held_preview, None
        if held is not None:
            self.emit(cancelled(held.job_id))
        if current is not None and current.profile == "preview":
            self._stop(current)

    def _stop(self, job: _Job) -> bool:
        """Cancel ``job``, kill its Manim and wait for its thread. Return whether it stopped.

        The wait ends 1 s after the job was first told to stop, so a job that
        outlived an earlier stop or cancel is not waited for again (D19).
        """
        self.log(f"stopping {job.job_id}")
        told = job.tell_to_stop()
        self._kill_process(job)
        job.thread.join(timeout=max(0.0, told + 1 - time.monotonic()))
        return not job.thread.is_alive()

    def _new_job(self, job_id: str, profile: ProfileName, *, force: bool = False) -> _Job:
        job = _Job(job_id, profile, threading.Thread(), threading.Event(), force=force)
        job.thread = threading.Thread(
            target=self._run_job,
            args=(job,),
            name=f"mathimator-{profile}-{job_id}",
            daemon=True,
        )
        return job

    def _launch_locked(self, job: _Job) -> None:
        """Make ``job`` the current job and start its thread. Caller holds ``_state_lock``."""
        self._current = job
        job.thread.start()

    def _take_held_locked(self, job_id: str) -> _Job | None:
        """Take the held job ``job_id`` out of its slot. Caller holds ``_state_lock``."""
        if self._held_export is not None and self._held_export.job_id == job_id:
            held, self._held_export = self._held_export, None
            return held
        if self._held_preview is not None and self._held_preview.job_id == job_id:
            held, self._held_preview = self._held_preview, None
            return held
        return None

    def _cancel(self, request: Request) -> None:
        """Cancel a running or held job (C7.3). Success writes no line; the target ends with ``error cancelled``."""
        target = request.string("jobId")
        with self._state_lock:
            current = self._current
            running = current if current is not None and current.job_id == target and not current.ended else None
            held = None if running is not None else self._take_held_locked(target)
            if running is not None:
                running.tell_to_stop()
        if running is not None:
            self.log(f"cancel {target}")
            self._kill_process(running)
        elif held is not None:
            self.log(f"cancel held {target}")
            self.emit(cancelled(target))
        else:
            self._reject(request.id, "no_such_job", "No render with that id is running.")

    def _kill_process(self, job: _Job) -> None:
        """End the job's Manim and every process it started. Never raises (F025)."""
        with self._state_lock:
            proc = job.proc
            if proc is None or proc.poll() is not None:
                return
            handle = job.win_job
            if handle:
                try:
                    # Under the lock, so the job thread cannot close the handle first.
                    terminate_job(handle)
                    return
                except OSError as exc:
                    self.log(f"job terminate failed: {exc}")
        try:
            terminate_process_group(proc)
        except OSError as exc:
            self.log(f"process tree terminate failed: {exc}")
            with contextlib.suppress(OSError):
                proc.kill()

    def _release_job(self, job: _Job) -> None:
        """Close the job's Job Object, whose kill-on-close ends whatever Manim left running.

        The handle leaves the job under the lock, so it is closed once, by
        whichever call takes it first (F023).
        """
        with self._state_lock:
            handle, job.win_job = job.win_job, None
        if handle:
            try:
                close_job(handle)
            except OSError as exc:
                self.log(f"job close failed: {exc}")

    def _clear_cache(self, request: Request) -> None:
        """Delete the derived files (C8.3). Files another program holds stay, and the reply says so (D58)."""
        with self._state_lock:
            # A held job waits behind the current one, so this covers held jobs too.
            busy = self._current is not None
        with self._doc_lock:
            session = self.session
            if session is None:
                event = self._no_project(request.id)
            elif busy:
                event = rejected(request.id, "render_in_progress", "A render is in progress.")
            else:
                try:
                    self._check_root_locked()
                except DocumentError as exc:
                    event = _refusal(request.id, exc)
                else:
                    left = self._remove_derived()
                    session.rendered.clear()
                    self._index = None
                    self._rebuild_index_locked()
                    self._save_sidecars_locked()
                    event = self._state_locked(request.id)
                    if left:
                        event["warning"] = {"code": "files_in_use", "message": FILES_IN_USE}
        self.emit(event)

    def _remove_derived(self) -> list[str]:
        """Delete the cache, the generated code, the glyph files and every log but the active one (D55).

        The active log is emptied. project.json, exports/, the lock and the
        temp-project marker stay. Return what could not be removed, each with
        the OS reason.
        """
        left: list[str] = []

        def collect(function: object, path: str, exc: BaseException) -> None:
            if not isinstance(exc, FileNotFoundError):
                left.append(f"{path}: {exc}")

        for folder in self.layout.derived_dirs:
            shutil.rmtree(folder, onexc=collect)
        logs = self.layout.log_path.parent
        if logs.is_dir():
            for child in list(logs.iterdir()):
                if child == self.log_path:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, onexc=collect)
                    continue
                try:
                    child.unlink()
                except OSError as exc:
                    collect(child.unlink, str(child), exc)
        with self._log_lock:
            try:
                os.truncate(self.log_path, 0)
            except OSError as exc:
                collect(os.truncate, str(self.log_path), exc)
        for item in left:
            self.log(f"could not remove {item}")
        return left

    def _run_job(self, job: _Job) -> None:
        try:
            self._run_job_inner(job)
        except Exception as exc:
            self._log_exception(f"job {job.job_id} failed")
            if not job.cancel.is_set():
                self._end(job, internal(job.job_id, exc))
        finally:
            if job.cancel.is_set():
                # A cancelled job returns at its next check without a line; this is its one terminal line.
                self._end(job, cancelled(job.job_id))
            self._finish_job(job)

    def _end(self, job: _Job, event: dict[str, object]) -> None:
        """Write the job's terminal line. A job gets exactly one; any later one is dropped (C2)."""
        with self._state_lock:
            if job.ended:
                return
            job.ended = True
        self.emit(event)

    def _finish_job(self, job: _Job) -> None:
        """Start the held export, or else the held preview, now that ``job`` has ended (C7.2)."""
        with self._state_lock:
            self._current = None
            if self._held_export is not None:
                held, self._held_export = self._held_export, None
            elif self._held_preview is not None:
                held, self._held_preview = self._held_preview, None
            else:
                return
            self.log(f"starting held {held.profile} {held.job_id}")
            self._launch_locked(held)

    def _render_running_in(self, layout: ProjectLayout) -> bool:
        with self._state_lock:
            current = self._current
            return current is not None and current.layout == layout

    def _run_job_inner(self, job: _Job) -> None:
        with self._doc_lock:
            if not self._prepare_job_locked(job):
                return
            argv = list(job.argv)
        if not self._clear_stale_media(job):
            return
        self.log(f"cwd: {job.manim_dir}")
        self.log("argv: " + json.dumps(argv))
        if job.cancel.is_set():
            return
        assert job.layout is not None
        marker = job.layout.run_marker
        if not self._write_run_marker(job, marker, time.time()):
            return
        try:
            self._run_manim(job, argv)
        finally:
            self._remove_run_marker(marker)

    def _clear_stale_media(self, job: _Job) -> bool:
        """Remove what this render must not reuse. Return False when it cannot start.

        Manim never empties the PNG-sequence folder (F021), and it reuses any
        partial movie that exists, complete or not (C14 Q3). TeX reads its
        inputs from Manim's TeX folder before its own, and Manim compiles any
        TeX source or DVI file it finds there, so a shared project could plant
        one that reads local files past the formula allowlist (D18, F076).
        """
        layout = job.layout
        assert layout is not None
        tex = layout.media_dir / "Tex"
        try:
            _remove_tex_inputs(tex)
        except OSError as exc:
            message = f"could not remove the old TeX files in {tex}: {exc}"
            self._end(job, error(job.job_id, "render_failed", message))
            return False
        if job.output_format == "png_sequence":
            frames = layout.png_frames_dir
            try:
                _remove_tree(frames)
            except OSError as exc:
                message = f"could not remove the old PNG frames in {frames}: {exc}"
                self._end(job, error(job.job_id, "render_failed", message))
                return False
        self._drop_incomplete_clips(layout.partial_movie_dir(job.quality))
        return True

    def _drop_incomplete_clips(self, folder: Path) -> None:
        for path in _segments(folder):
            if not self._clips.complete(path):
                self._remove_clip(path)

    def _remove_clip(self, path: Path) -> bool:
        """Delete an incomplete clip. Return False when it is still there."""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.log(f"could not remove incomplete clip {path}: {exc}")
            return False
        self.log(f"removed incomplete clip {path}")
        return True

    def _write_run_marker(self, job: _Job, marker: Path, started: float) -> bool:
        """Record the run before Manim starts, so a later bind can sweep it (G1.1, G1.2)."""
        temporary = marker.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps({"started": started}) + "\n", encoding="utf-8", newline="\n")
            os.replace(temporary, marker)
        except OSError as exc:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            self._end(job, error(job.job_id, "render_failed", f"could not write {marker}: {exc}"))
            return False
        return True

    def _remove_run_marker(self, marker: Path) -> None:
        try:
            marker.unlink(missing_ok=True)
        except OSError as exc:
            self.log(f"could not remove {marker}: {exc}")

    def _run_manim(self, job: _Job, argv: list[str]) -> None:
        assert job.layout is not None and job.manim_dir is not None
        tex = job.layout.media_dir / "Tex"
        if sys.platform == "win32":
            # start_new_session is ignored on Windows, so the grandchild would
            # stay in this console and could open its own window. A new process
            # group plus a hidden window keeps cancel and the single-window
            # rule on the subprocess path.
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            start_new_session = False
        else:
            startupinfo = None
            creationflags = 0
            start_new_session = True
        # What the folders hold before Manim starts, which tells the files this run writes.
        progress = ManimProgress(job.video_dir, self._clips, job.ready, job.waiting)
        tex_logs = file_stamps(tex, "*.log")
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=job.manim_dir,
                env=manim_environment(os.environ, job.manim_dir, self._venv_launcher),
                bufsize=1,
                startupinfo=startupinfo,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError as exc:
            self._end(job, error(job.job_id, "render_failed", f"Could not start Manim: {exc}"))
            return
        with self._state_lock:
            job.proc = proc
        try:
            output, return_code = self._supervise(job, proc, progress)
        except BaseException:
            # Whatever failed, Manim must not run on unwatched with its pipes filling up (F097).
            self._kill_process(job)
            raise
        finally:
            self._release_job(job)
        if job.cancel.is_set():
            self._discard(progress)
        elif return_code != 0:
            # Manim may have been killed from outside or crashed natively, so
            # its newest partial can be cut off (G1.1).
            self._discard(progress)
            tex_log = failed_tex_log(written_since(tex_logs, file_stamps(tex, "*.log")))
            message = format_render_failure(return_code, output, tex_log)
            self._end(job, error(job.job_id, "render_failed", message, output.rstrip() or None))
        else:
            self._finish_run(job, progress, output)

    def _supervise(self, job: _Job, proc: subprocess.Popen[str], progress: ManimProgress) -> tuple[str, int]:
        """Watch one Manim run to its end. Return the last lines it wrote and its exit code."""
        handle: int | None = None
        if jobs_enabled():
            try:
                handle = assign_kill_on_close(proc.pid)
            except OSError as exc:
                self.log(f"job object assignment failed: {exc}")
        with self._state_lock:
            job.win_job = handle
            stopped = job.cancel.is_set()
        # Logged once the job holds it, so the log write does not widen the window before the assignment.
        self.log(f"manim started pid={proc.pid}")
        if handle:
            self.log(f"job assigned pid={proc.pid}")
        elif sys.platform == "win32":
            # Without a Job Object, a cancel walks the process table. Its helper
            # starts now, beside Manim, and never at startup (D15).
            threading.Thread(target=self._warm_process_control, name="process-table", daemon=True).start()
        if stopped:
            self._kill_process(job)
        else:
            self.emit({"event": "started", "jobId": job.job_id})

        # Every line goes to worker.log; the last ones are a failure's details (D23).
        tail: deque[str] = deque(maxlen=_TAIL_LINES)
        tail_lock = threading.Lock()

        def drain(stream: IO[str]) -> None:
            # To the end, cancelled or not, so Manim never blocks on a full pipe (F036).
            for line in stream:
                with tail_lock:
                    tail.append(line)
                self.log("manim: " + line.rstrip("\n"))

        assert proc.stdout is not None and proc.stderr is not None
        readers = [threading.Thread(target=drain, args=(stream,), daemon=True) for stream in (proc.stdout, proc.stderr)]
        for reader in readers:
            reader.start()
        return_code = self._wait(job, proc, progress)
        self._release_job(job)
        # A cancelled job does not wait for a child that escaped the kill and still holds a pipe (F036).
        deadline = time.monotonic() + _DRAIN_SECONDS if job.cancel.is_set() else None
        for reader in readers:
            reader.join(timeout=None if deadline is None else max(0.0, deadline - time.monotonic()))
        with tail_lock:
            return "".join(tail), return_code

    def _wait(self, job: _Job, proc: subprocess.Popen[str], progress: ManimProgress) -> int:
        """Wait for Manim to exit, sending each preview beat it finishes on the way (C7.2). Return its exit code."""
        while True:
            try:
                return proc.wait(timeout=_POLL_SECONDS)
            except subprocess.TimeoutExpired:
                if job.profile == "preview" and not job.cancel.is_set():
                    self._report_beats(job, progress.poll())

    def _discard(self, progress: ManimProgress) -> None:
        """Delete what a cancelled or failed run left that may be cut off, and log it (D13, G5.10)."""
        removed, failed = progress.discard()
        for path in removed:
            self.log(f"removed unfinished media {path}")
        for path, exc in failed:
            self.log(f"could not remove unfinished media {path}: {exc}")

    def _finish_run(self, job: _Job, progress: ManimProgress, output: str) -> None:
        """Check what a run that exited 0 wrote, then record the preview or publish the export."""
        # A PNG sequence writes no segments, so a segment list in its quality folder is another render's.
        segments = [] if job.output_format == "png_sequence" else progress.segments()
        incomplete = [path for path in segments if not self._clips.complete(path)]
        if incomplete:
            # Manim reused or wrote a clip that is cut off, so its output is
            # suspect. Delete exactly those clips, so the next render redoes their beats.
            left = [path for path in incomplete if not self._remove_clip(path)]
            if left:
                message = f"manim used an incomplete clip {left[0]} that could not be removed"
            else:
                message = f"manim used an incomplete clip {incomplete[0].name}; it was removed, so render again"
            self._end(job, error(job.job_id, "render_failed", message))
            return
        movie = self._find_output(job)
        if movie is None or not _artifact_ready(movie):
            self._end(job, error(job.job_id, "render_failed", "manim exited 0 without a movie"))
            return
        if job.output_format == "png_sequence":
            problem = frame_count_problem(movie, output)
            if problem is not None:
                self._end(job, error(job.job_id, "render_failed", problem))
                return
        if job.profile == "export":
            self._finish_export(job, movie)
        else:
            self._finish_preview(job, movie, segments, progress.finish(segments))

    def _report_beats(self, job: _Job, beats: list[tuple[int, Path]]) -> None:
        """Record finished preview beats in the index and send their ``beat_ready`` lines (C7.1). Exports send none.

        Both happen under the document lock, which is taken before the stdout
        lock. An edit, undo, reload or bind therefore either commits first, and
        no line is sent, or its reply follows the lines on stdout (F135).
        """
        if not beats:
            return
        with self._doc_lock:
            if not self._may_report_locked(job):
                return
            if job.capture_keys is not None:
                assert self._index is not None
                self._index.record(beats, job.capture_keys)
                self._write_index_locked()
            self._emit_beats(job, beats)

    def _finish_preview(self, job: _Job, movie: Path, segments: list[Path], beats: list[tuple[int, Path]]) -> None:
        """Record the finished preview, send the beats not sent yet, then ``done``. No ``state`` line follows (C5).

        The segment list holds the full beat order, so it settles every partial
        the run recorded while it ran.
        """
        with self._doc_lock:
            if self._may_report_locked(job):
                if job.capture_keys is not None and self.session is not None and self._index is not None:
                    self._index.record(enumerate(segments), job.capture_keys)
                    self._index.output = str(movie)
                    self.session.mark_rendered("preview")
                    self._save_sidecars_locked()
                self._emit_beats(job, beats)
            elif self._index is not None and self._index.output == str(movie):
                # The run overwrote the movie of an earlier preview with one of another document (F010).
                self._index.output = None
                self._write_index_locked()
        if not job.cancel.is_set():
            self._end(job, {"event": "done", "jobId": job.job_id, "path": str(movie)})

    def _may_report_locked(self, job: _Job) -> bool:
        """Whether ``job`` may record and send its beats (C7.1). Caller holds ``_doc_lock``.

        The job is not cancelled and belongs to the folder bound now. A
        project-mode preview must also render the document open now; a
        hand-written one has none.
        """
        if job.cancel.is_set() or job.generation != self._generation:
            return False
        return job.capture_keys is None or self._matches_locked(job)

    def _matches_locked(self, job: _Job) -> bool:
        """Whether ``job`` renders the document open now, beat for beat. Caller holds ``_doc_lock``."""
        return self.session is not None and job.capture_keys == beat_chain_keys(self.session.project, "preview")

    def _emit_beats(self, job: _Job, beats: Iterable[tuple[int, Path]]) -> None:
        for index, path in beats:
            self.emit({"event": "beat_ready", "jobId": job.job_id, "beatIndex": index, "path": str(path)})

    def _finish_export(self, job: _Job, movie: Path) -> None:
        """Publish the finished export, then send ``done`` with whether it is the current export (C7.1, C7.2)."""
        captured = job.captured
        if job.cancel.is_set():
            return
        if captured is None:
            # Hand-written mode: Manim's own output, with no copy in exports/ and no current (C7.7).
            self._end(job, {"event": "done", "jobId": job.job_id, "path": str(movie)})
            return
        assert job.layout is not None
        try:
            # Outside the document lock, so edits do not wait for the copy (F031, F164).
            published = publish_export(job.layout, captured, movie, job.quality)
        except PublishFailed as exc:
            self._end(job, error(job.job_id, "publish_failed", str(exc)))
            return
        with self._doc_lock:
            current = self.session is not None and export_key(captured) == export_key(self.session.project)
        self._end(job, {"event": "done", "jobId": job.job_id, "path": str(published), "current": current})

    def _prepare_job_locked(self, job: _Job) -> bool:
        """Run the start checks of C7.2 in order, then write the scene and the argv.

        Return False when the job has ended without Manim.
        """
        layout = job.layout = self.layout
        job.generation = self._generation
        if self.session is None:
            return self._prepare_handwritten_locked(job)
        project = self.session.project
        try:
            # The start never writes project.json: every edit has saved it, and
            # a file changed outside Mathimator must not be overwritten (D59).
            self._check_disk_locked()
            source = _scene_source(project)
            latex = needs_latex(project)
            if latex and self._tex_error is not None:
                raise DocumentError(self._tex_error, code="tex_unavailable")
            root = manim_root(layout.root, latex=latex)
        except DocumentError as exc:
            self._end(job, _refusal(job.job_id, exc))
            return False
        make_folders(layout.root, layout.media_dir)
        export_format = "mp4"
        export_transparent = False
        if job.profile == "export":
            export_format = project.export_profile.format
            export_transparent = project.export_profile.transparent
            # Re-export skips the cache; the older file stays until the new one replaces it (D56).
            existing = None if job.force else existing_export(layout, project)
            if existing is not None:
                self.log("export cache hit")
                self._end(job, {"event": "done", "jobId": job.job_id, "path": str(existing), "current": True})
                return False
        # A shared project folder could carry a planted codegen/__pycache__ or a
        # module that shadows one Manim imports. Manim imports scene.py from
        # this folder, and runs in it, so it holds nothing but the scene just
        # written (F170, D60).
        codegen = layout.scene_path.parent
        try:
            _remove_tree(codegen)
            make_folders(layout.root, codegen)
            layout.scene_path.write_text(source, encoding="utf-8", newline="\n")
        except OSError as exc:
            self._end(job, error(job.job_id, "render_failed", f"could not write {layout.scene_path}: {exc}"))
            return False
        job.manim_dir = root / codegen.relative_to(layout.root)
        job.captured = load_text(dumps_project(project))
        if job.profile == "preview":
            width = project.preview_profile.width
            height = project.preview_profile.height
            fps = project.preview_profile.fps
            self._rebuild_index_locked()
            self._copy_last_good_locked()
            self._save_sidecars_locked()
            assert self._index is not None
            job.capture_keys = beat_chain_keys(project, "preview")
            job.ready = {beat.index: Path(beat.partial) for beat in self._index.beats if beat.fresh and beat.partial}
            job.waiting = [beat.index for beat in self._index.beats if not beat.fresh]
        else:
            width = project.export_profile.width
            height = project.export_profile.height
            fps = project.export_profile.fps
        job.quality = f"{height}p{fps}"
        job.argv = build_manim_argv(
            self._manim_python,
            job.profile,
            str(project.seed),
            width=width,
            height=height,
            fps=fps,
            fmt=export_format,
            transparent=export_transparent,
        )
        return True

    def _prepare_handwritten_locked(self, job: _Job) -> bool:
        """Render the scene.py already in the folder (C7.7), with no document, checks or index."""
        if not self._handwritten:
            # D42: a project.json that was refused never renders a scene.py left from before.
            self._end(job, self._no_project(job.job_id))
            return False
        layout = self.layout
        if not layout.scene_path.is_file():
            message = f"No project is open, and {layout.scene_path} does not exist."
            self._end(job, rejected(job.job_id, "no_project", message))
            return False
        make_folders(layout.root, layout.media_dir)
        _width, height, fps = profile_size(job.profile)
        job.quality = f"{height}p{fps}"
        job.manim_dir = layout.scene_path.parent
        job.argv = build_manim_argv(self._manim_python, job.profile)
        return True

    def _copy_last_good_locked(self) -> None:
        """Copy each stale beat's newest file into last-good/, where the next render cannot replace it (spec §6)."""
        if self._index is None:
            return
        layout = self.layout
        make_folders(layout.root, layout.last_good_dir)
        for beat in self._index.beats:
            if beat.fresh or beat.last_good is None:
                continue
            source = Path(beat.last_good)
            # Reading the index kept only paths inside these folders; check again before reading the file (F035).
            if not any(source.is_relative_to(folder) for folder in (layout.media_dir, layout.last_good_dir)):
                continue
            if not source.is_file():
                continue
            dest = layout.last_good_dir / f"{beat.id}.mp4"
            if source.resolve() != dest.resolve():
                shutil.copyfile(source, dest)
            beat.last_good = str(dest)

    def _rebuild_index_locked(self) -> None:
        """Fit the preview index to the document (``preview_index.rebuild_index``). Caller holds ``_doc_lock``."""
        session = self.session
        if session is None:
            self._index = None
            return
        project = session.project
        self._index = rebuild_index(
            self._index,
            project.scene.beats,
            beat_chain_keys(project, "preview"),
            self._clips.complete,
            lambda: session.output_current("preview"),
        )

    def _check_root_locked(self) -> _Binding:
        """Check 1 of C8.1: the bound folder is still there, with the identity it was bound with (D57)."""
        binding = self._binding
        if binding is None or folder_identity(self.project) != binding.identity:
            raise DocumentError(PROJECT_UNAVAILABLE, code="project_unavailable")
        return binding

    def _check_file_locked(self) -> _Binding:
        """Checks 1 and 2 of C8.1: the folder, and a project.json in it."""
        binding = self._check_root_locked()
        if not self.layout.project_file.exists():
            raise DocumentError(PROJECT_UNAVAILABLE, code="project_unavailable")
        return binding

    def _read_disk_locked(self) -> tuple[_Binding, bytes]:
        """Checks 1 to 3 of C8.1: the folder, then the bytes of its project.json (G5.9)."""
        binding = self._check_file_locked()
        return binding, _read_project_bytes(self.layout.project_file)

    def _check_disk_locked(self) -> _Binding:
        """The checks of C8.1 before a write or a render, ending with the fingerprint (D59)."""
        binding, data = self._read_disk_locked()
        if fingerprint(data) != binding.fingerprint:
            raise DocumentError(PROJECT_CHANGED, code="project_changed")
        return binding

    def _save_locked(self, payload: str, *, overwrite: bool = False) -> None:
        """Write ``payload`` as project.json after the checks of C8.1, and take its fingerprint.

        ``overwrite`` skips the fingerprint check (D59). Raises DocumentError,
        leaving the file and the fingerprint as they were (D05).
        """
        binding = self._check_file_locked() if overwrite else self._check_disk_locked()
        data = payload.encode("utf-8")
        try:
            write_atomically(self.layout.project_file, data)
        except OSError as exc:
            raise _save_failed(exc) from exc
        binding.fingerprint = fingerprint(data)

    def _save_sidecars_locked(self) -> None:
        if self.session is None:
            return
        self._write_record(self.layout.rendered_path, self.session.rendered_payload())
        self._write_index_locked()

    def _write_index_locked(self) -> None:
        if self._index is None:
            return
        self._write_record(self.layout.index_path, self._index.stored(self.project))

    def _write_record(self, path: Path, value: object) -> None:
        """Write one cache record. A record that cannot be written is logged, not fatal (C8.1)."""
        try:
            make_folders(self.project, path.parent)
            path.write_text(dumps_record(value), encoding="utf-8", newline="\n")
        except OSError as exc:
            self.log(f"could not write {path}: {exc}")

    def _read_rendered(self) -> object:
        return read_record(self.layout.rendered_path)

    def _read_index(self) -> PreviewIndex | None:
        """The stored index, keeping only paths inside this project's media and last-good folders (D18, F035)."""
        layout = self.layout
        return load_index(read_record(layout.index_path), layout.root, (layout.media_dir, layout.last_good_dir))

    def _find_output(self, job: _Job) -> Path | None:
        if job.layout is None or not job.quality:
            return None
        return find_export_output(job.layout, job.quality, job.output_format)


def _segments(folder: Path) -> list[Path]:
    """The partial movies in ``folder``, which may not exist."""
    if not folder.is_dir():
        return []
    return [path for path in folder.iterdir() if path.suffix.lower() in SEGMENT_SUFFIXES and path.is_file()]


def _read_project_bytes(project_file: Path) -> bytes:
    """The bytes of project.json, or ``read_failed`` with the OS reason (G5.9)."""
    try:
        return project_file.read_bytes()
    except OSError as exc:
        raise DocumentError(f"Could not read project.json: {os_reason(exc)}.", code="read_failed") from exc


def _lock_folder(folder: Path) -> ProjectLock:
    """Take ``folder``'s project lock (D58), or refuse with ``project_locked`` or ``lock_failed``."""
    try:
        return ProjectLock.acquire(folder)
    except ProjectLocked as exc:
        raise DocumentError(PROJECT_LOCKED, code="project_locked") from exc
    except OSError as exc:
        raise DocumentError(f"Mathimator cannot write to {folder}: {os_reason(exc)}.", code="lock_failed") from exc


def _rotate_log(log_path: Path) -> None:
    """Move a full log to ``<log>.1``, replacing the older copy (D55).

    A log that another program holds open is rotated by a later line.
    """
    with contextlib.suppress(OSError):
        if log_path.stat().st_size >= LOG_LIMIT:
            os.replace(log_path, log_path.with_name(f"{log_path.name}.1"))


def _save_failed(exc: OSError) -> DocumentError:
    return DocumentError(f"Could not save project.json: {os_reason(exc)}. The change was undone.", code="save_failed")


def _save_as_failed(target: Path, reason: str) -> str:
    return f"Could not save the project to {target}: {reason}."


def _refusal(job_id: str, exc: DocumentError) -> dict[str, object]:
    return rejected(job_id, exc.code, str(exc), exc.field)


def _scene_source(project: Project) -> str:
    """The generated scene.py, or ``invalid_document`` with the codegen message (C7.2)."""
    try:
        return generate(project)
    except CodegenError as exc:
        raise DocumentError(str(exc)) from exc


def _marker_start(marker: Path) -> float | None:
    """Return the start time a run marker records, or None when there is no marker.

    A marker that cannot be read counts from its own mtime, which is just
    before the run started.
    """
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        raw = None
    started = raw.get("started") if isinstance(raw, dict) else None
    if isinstance(started, (int, float)) and not isinstance(started, bool) and math.isfinite(started):
        return float(started)
    try:
        return marker.stat().st_mtime
    except OSError:
        return None


def _remove_tree(path: Path) -> None:
    """Delete a folder that may not exist, retrying for about 0.5 s while Windows reports it in use."""
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1)


def _remove_tex_inputs(tex: Path) -> None:
    """Delete everything in Manim's TeX folder but the SVG files it reuses.

    Manim writes a TeX source and its DVI file again when it needs them. A
    link goes, never what it points to: ``remove_file`` removes a link to a
    folder on Windows too.
    """
    if tex.is_symlink() or tex.is_junction():
        remove_file(tex)
        return
    if not tex.is_dir():
        return
    for path in list(tex.iterdir()):
        if path.suffix == ".svg" and path.is_file(follow_symlinks=False):
            continue
        if path.is_dir(follow_symlinks=False) and not path.is_junction():
            _remove_tree(path)
        else:
            remove_file(path)


def _artifact_ready(path: Path) -> bool:
    if path.is_file():
        return True
    if path.is_dir():
        return any(path.glob("*.png"))
    return False
