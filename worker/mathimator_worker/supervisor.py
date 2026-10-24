"""Long-lived stdio supervisor. One Manim grandchild at a time.

It speaks protocol 2 (``protocol.py``). The commands are ``status``,
``get_state``, ``edit``, ``undo``, ``redo``, ``code``, ``clear_cache``,
``open_project``, ``reload``, ``overwrite``, ``save_as``, ``render_preview``,
``export`` and ``cancel``. Every request gets exactly one reply line, except
the jobs and ``cancel``: a job sends ``started``, ``beat_ready`` (previews
only) and one terminal ``done``, ``error`` or ``rejected``. An unexpected
exception is answered with ``error internal`` and the worker keeps serving;
every state change is all-or-nothing, so nothing is left half changed (D03).

The worker is bound to at most one project folder (contract C8). While bound
it holds the folder's lock (D58), the folder's identity (D57) and the SHA-256
of project.json as last read or written (D59). A write or a render first
checks that the folder is still there and that project.json is unchanged on
disk, and refuses otherwise. Startup never exits on a project problem: the
worker stays unbound and says why in ``ready.startupError``. The worker never
creates a project folder and never writes an empty project.json over a missing
one.

This process does not import Manim or PyAV. A new preview cancels an in-flight
preview. An export cancels an in-flight preview and then runs. While an export
runs, the newest preview request waits. Document edits never cancel an export
and never start one.

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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from mathimator_worker.argv import build_manim_argv, profile_size
from mathimator_worker.clip_check import SEGMENT_SUFFIXES, ClipChecker
from mathimator_worker.codegen import CodegenError, generate
from mathimator_worker.document import (
    MANIM_VERSION,
    Project,
    beat_cache_keys,
    derived_starts,
    dumps_project,
    empty_project,
    export_artifact_name,
    export_folder_name,
    export_key,
    load_bytes,
    load_text,
    scene_hash,
    scene_stamp,
)
from mathimator_worker.document_error import DocumentError
from mathimator_worker.preview_index import IndexBeat, PreviewIndex, load_index
from mathimator_worker.process_group import terminate_process_group, warm_process_table
from mathimator_worker.progress import ManimProgress, discard_unfinished_partials
from mathimator_worker.project_folder import (
    ProjectLock,
    ProjectLocked,
    fingerprint,
    folder_identity,
    make_folders,
    os_reason,
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
    rejected,
)
from mathimator_worker.session import EditResult, Session
from mathimator_worker.tex_failure import format_render_failure
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


@dataclass
class _Job:
    job_id: str
    profile: str
    thread: threading.Thread
    cancel: threading.Event
    proc: subprocess.Popen[str] | None = None
    win_job: int | None = None
    project_dir: Path | None = None
    scene_path: Path | None = None
    media_dir: Path | None = None
    run_marker: Path | None = None
    argv: list[str] = field(default_factory=list)
    quality: str = ""
    # The binding generation the job started under (C7.1).
    generation: int = 0
    captured: Project | None = None
    capture_stamp: str | None = None
    capture_keys: list[str] | None = None
    # Set with the job's one terminal line (done, error or rejected).
    ended: bool = False

    @property
    def output_format(self) -> str:
        if self.profile == "export" and self.captured is not None:
            return self.captured.export_profile.format
        return "mp4"


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
        # Resolve like _open_project does. Windows may hand over an 8.3 alias
        # such as C:\Users\JOHNSM~1, and TeX stops reading a path at "~" (G4.1).
        self.project = project.resolve()
        self._log_override = log_path
        self._log_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._doc_lock = threading.Lock()
        self._stdout_lock = threading.Lock()
        self._current: _Job | None = None
        self._pending_preview: str | None = None
        self.session: Session | None = None
        self._index: PreviewIndex | None = None
        self._binding: _Binding | None = None
        # Counts the binds. A job records the one it started under (C7.1).
        self._generation = 0
        # Why there is no session: the refusal of the startup project.json, or not_a_project in hand-written mode.
        self._startup_error: DocumentError | None = None
        self._handwritten = False
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
        self._bind_paths()
        self._start(init)

    def _start(self, init: bool) -> None:
        """Bind the startup folder in the order of C8.2. A refusal leaves the worker unbound and serving."""
        identity = folder_identity(self.project)
        if identity is None:
            # Nothing is created, and the log goes to stderr (G1.3).
            self._startup_error = DocumentError(PROJECT_UNAVAILABLE, code="project_unavailable")
            return
        if not init and not self.project_file.exists():
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
        """Stop the running job and release the project lock, as exiting does."""
        self._cancel_current()
        with self._doc_lock:
            if self._binding is not None:
                self._binding.lock.release()

    def _bind_paths(self) -> None:
        root = self.project / ".mathimator"
        self.scene_path = root / "codegen" / "scene.py"
        self.media_dir = root / "cache" / "media"
        self.last_good_dir = root / "cache" / "last-good"
        self.rendered_path = root / "cache" / "rendered.json"
        self.index_path = root / "cache" / "preview-index.json"
        self.run_marker = root / "cache" / "manim-run.json"
        self.project_file = self.project / "project.json"
        if self._log_override is not None:
            self.log_path = self._log_override
        else:
            self.log_path = root / "logs" / "worker.log"

    def _sweep_interrupted_run(self) -> None:
        """Remove what a Manim run cut off by a quit, a crash or a kill left behind (G1.1)."""
        started = _marker_start(self.run_marker)
        if started is None:
            return
        self.log(f"sweeping media from an interrupted run started={started}")
        try:
            removed = discard_unfinished_partials(self.media_dir, started, set())
        except OSError as exc:
            # The marker stays, so the next bind sweeps again.
            self.log(f"could not sweep {self.media_dir}: {exc}")
            return
        for path in removed:
            self.log(f"removed unfinished media {path}")
        self._remove_run_marker(self.run_marker)

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
        # Read once: a bind on another thread changes the project and then its log path.
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
        self.log(f"supervisor listening project={self.project} python={self.python}")
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
        project_file = folder / "project.json"
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
        kept = (self.project, previous, old_session, self._index, self._handwritten, self._startup_error)
        try:
            self.project, self._binding, self.session = folder, binding, session
            self._handwritten = False
            self._startup_error = None
            self._bind_paths()
            if not self._render_running_in(self.media_dir):
                self._sweep_interrupted_run()
            if session is not old_session:
                session.load_rendered(self._read_rendered())
            self._index = self._read_index()
            self._rebuild_index_locked()
            self._save_sidecars_locked()
        except BaseException:
            self.project, self._binding, self.session, self._index, self._handwritten, self._startup_error = kept
            self._bind_paths()
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
        return self.session.state_event(job_id, str(self.project))

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
        if identity is None or not (init or (folder / "project.json").exists()):
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
                write_atomically(target / "project.json", data)
                undo.callback(remove_quietly, target / "project.json")
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
        exports = self.project / ".mathimator" / "exports"
        for source in (self.media_dir, self.last_good_dir, exports):
            if not source.is_dir():
                continue
            try:
                shutil.copytree(source, target / source.relative_to(self.project))
            except shutil.Error as exc:
                # copytree reports each file it could not copy as text. The first one says why.
                raise OSError(exc.args[0][0][2]) from exc
        with self._doc_lock:
            records: dict[Path, object] = {self.rendered_path: session.rendered_payload()}
            if self._index is not None:
                records[self.index_path] = self._index.stored(self.project)
        for path, value in records.items():
            copy = target / path.relative_to(self.project)
            make_folders(target, copy.parent)
            copy.write_text(_record_text(value), encoding="utf-8", newline="\n")

    def _export_running(self) -> bool:
        with self._state_lock:
            current = self._busy_locked()
        return current is not None and current.profile == "export"

    def _attach_focus(self, payload: dict[str, object], result: EditResult) -> None:
        if result.focus_beat_id is None and result.focus_object_id is None:
            return
        payload["focus"] = {
            "beatId": result.focus_beat_id,
            "objectId": result.focus_object_id,
        }

    def _render_preview(self, request: Request) -> None:
        with self._state_lock:
            current = self._busy_locked()
            held = current is not None and current.profile == "export"
            replaced = self._pending_preview if held else None
            if held:
                self._pending_preview = request.id
        if not held:
            self._start_after(current, request.id, "preview")
            return
        self.log(f"preview {request.id} waiting for export")
        if replaced is not None:
            # The newer preview takes the held slot; the one it replaces is cancelled (C7.3).
            self.emit(cancelled(replaced))

    def _export(self, request: Request) -> None:
        with self._state_lock:
            current = self._busy_locked()
        if current is not None and current.profile == "export":
            self._reject(request.id, "export_running", EXPORT_RUNNING)
            return
        self._start_after(current, request.id, "export")

    def _start_after(self, current: _Job | None, job_id: str, profile: str) -> None:
        """Start a job once ``current``, a running preview, has stopped."""
        if current is not None and not self._stop(current):
            self._emit_error(job_id, "render_failed", "Could not stop the previous render.")
            return
        self._start_job(job_id, profile)

    def _stop_previews(self) -> None:
        """Before a switch: cancel a held preview and stop a running one (C8.3).

        The switch goes ahead even if the running preview is still alive after 1 s.
        """
        with self._state_lock:
            current = self._busy_locked()
            held, self._pending_preview = self._pending_preview, None
        if held is not None:
            self.emit(cancelled(held))
        if current is not None and current.profile == "preview":
            self._stop(current)

    def _stop(self, job: _Job) -> bool:
        """Cancel ``job``, kill its Manim and wait up to 1 s for its thread. Return whether it stopped."""
        self.log(f"stopping {job.job_id}")
        job.cancel.set()
        self._kill_process(job)
        job.thread.join(timeout=1)
        return not job.thread.is_alive()

    def _busy_locked(self) -> _Job | None:
        """The current job while its thread runs. Caller holds ``_state_lock``."""
        current = self._current
        return current if current is not None and current.thread.is_alive() else None

    def _start_job(self, job_id: str, profile: str) -> None:
        with self._state_lock:
            self._install_job_locked(job_id, profile)

    def _install_job_locked(self, job_id: str, profile: str) -> None:
        job = _Job(
            job_id=job_id,
            profile=profile,
            thread=threading.Thread(target=lambda: None),
            cancel=threading.Event(),
        )
        job.thread = threading.Thread(
            target=self._run_job,
            args=(job,),
            name=f"mathimator-{profile}-{job_id}",
            daemon=True,
        )
        self._current = job
        job.thread.start()

    def _cancel(self, request: Request) -> None:
        """Cancel a running or held job (C7.3). Success writes no line; the target ends with ``error cancelled``."""
        target = request.string("jobId")
        with self._state_lock:
            current = self._busy_locked()
            running = current if current is not None and current.job_id == target and not current.ended else None
            held = running is None and self._pending_preview == target
            if running is not None:
                running.cancel.set()
            if held:
                self._pending_preview = None
        if running is not None:
            self.log(f"cancel {target}")
            self._kill_process(running)
        elif held:
            self.log(f"cancel held {target}")
            self.emit(cancelled(target))
        else:
            self._reject(request.id, "no_such_job", "No render with that id is running.")

    def _cancel_current(self) -> None:
        with self._state_lock:
            current = self._current
        if current is not None:
            current.cancel.set()
            self._kill_process(current)

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

    def _discard_partials(self, job: _Job, started: float, finished: set[Path]) -> None:
        media = job.media_dir
        scene_path = job.scene_path
        if media is None or scene_path is None:
            return
        # The log names MP4 segments only, so keep every complete segment this
        # run wrote, whatever its container (C14 Q3).
        segments = _segments(partial_movie_dir(media, scene_path.stem, job.quality))
        keep = finished | {path for path in segments if self._clips.complete(path)}
        for path in discard_unfinished_partials(media, started, keep):
            self.log(f"removed unfinished media {path}")

    def _release_job(self, job: _Job) -> None:
        """Close the job's Job Object, whose kill-on-close ends whatever Manim left running."""
        with self._state_lock:
            handle, job.win_job = job.win_job, None
        if handle:
            close_job(handle)

    def _clear_cache(self, request: Request) -> None:
        """Delete the derived files (C8.3). Files another program holds stay, and the reply says so (D58)."""
        with self._state_lock:
            busy = self._busy_locked() is not None or self._pending_preview is not None
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

        state = self.project / ".mathimator"
        for name in ("cache", "codegen", "equation", "typst"):
            shutil.rmtree(state / name, onexc=collect)
        logs = state / "logs"
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
        with self._state_lock:
            if self._current is job:
                self._current = None
            pending: str | None = None
            if job.profile == "export":
                pending = self._pending_preview
                self._pending_preview = None
            if pending is None:
                return
            self.log(f"starting queued preview {pending}")
            self._install_job_locked(pending, "preview")

    def _still_current(self, job: _Job) -> bool:
        with self._state_lock:
            return self._current is job and not job.cancel.is_set()

    def _render_running_in(self, media_dir: Path) -> bool:
        with self._state_lock:
            current = self._current
            return current is not None and current.thread.is_alive() and current.media_dir == media_dir

    def _run_job_inner(self, job: _Job) -> None:
        with self._doc_lock:
            if not self._prepare_job_locked(job):
                return
            argv = list(job.argv)
        if not self._clear_stale_media(job):
            return
        self.log("argv: " + json.dumps(argv))
        if not self._still_current(job):
            return
        marker = job.run_marker
        assert marker is not None
        started = time.time()
        if not self._write_run_marker(job, marker, started):
            return
        try:
            self._run_manim(job, argv, started)
        finally:
            self._remove_run_marker(marker)

    def _clear_stale_media(self, job: _Job) -> bool:
        """Remove what this render must not reuse. Return False when it cannot start.

        Manim never empties the PNG-sequence folder (F021), and it reuses any
        partial movie that exists, complete or not (C14 Q3).
        """
        media_dir = job.media_dir
        scene_path = job.scene_path
        assert media_dir is not None and scene_path is not None
        if job.output_format == "png_sequence":
            frames = png_frames_dir(media_dir, scene_path.stem)
            try:
                _remove_tree(frames)
            except OSError as exc:
                message = f"could not remove the old PNG frames in {frames}: {exc}"
                self._end(job, error(job.job_id, "render_failed", message))
                return False
        self._drop_incomplete_clips(partial_movie_dir(media_dir, scene_path.stem, job.quality))
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

    def _run_manim(self, job: _Job, argv: list[str], started: float) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["COLUMNS"] = "240"
        env["NO_COLOR"] = "1"
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
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
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
            self._supervise(job, proc, started)
        except BaseException:
            # Whatever failed, Manim must not run on unwatched with its pipes filling up (F097).
            self._kill_process(job)
            raise
        finally:
            self._release_job(job)

    def _supervise(self, job: _Job, proc: subprocess.Popen[str], started: float) -> None:
        """Watch one Manim run to its end and write the job's lines."""
        handle: int | None = None
        if jobs_enabled():
            try:
                handle = assign_kill_on_close(proc.pid)
            except OSError as exc:
                self.log(f"job object assignment failed: {exc}")
        with self._state_lock:
            job.win_job = handle
            stopped = job.cancel.is_set() or self._current is not job
        if handle:
            self.log(f"job assigned pid={proc.pid}")
        elif sys.platform == "win32":
            # Without a Job Object, a cancel walks the process table. Its helper
            # starts now, beside Manim, and never at startup (D15).
            threading.Thread(target=self._warm_process_control, name="process-table", daemon=True).start()
        if stopped:
            self._kill_process(job)
            self._discard_partials(job, started, set())
            return
        self.emit({"event": "started", "jobId": job.job_id})

        media_dir = job.media_dir
        assert media_dir is not None
        progress = ManimProgress(media_dir)
        captured: list[str] = []
        capture_lock = threading.Lock()
        progress_lock = threading.Lock()

        def consume(stream: IO[str]) -> None:
            for line in stream:
                text = str(line)
                with capture_lock:
                    captured.append(text)
                self.log("manim: " + text.rstrip("\n"))
                with progress_lock:
                    ready = progress.feed(text)
                for index, path in ready:
                    if not self._still_current(job):
                        return
                    self._report_beat(job, index, path)

        assert proc.stdout is not None and proc.stderr is not None
        stdout_thread = threading.Thread(target=consume, args=(proc.stdout,), daemon=True)
        stderr_thread = threading.Thread(target=consume, args=(proc.stderr,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        return_code = proc.wait()
        self._release_job(job)
        stdout_thread.join()
        stderr_thread.join()

        with progress_lock:
            finished = set(progress.beats.values())
            if progress.output_path is not None:
                finished.add(progress.output_path)
        if not self._still_current(job):
            self._discard_partials(job, started, finished)
            return
        if return_code != 0:
            # Manim may have been killed from outside or crashed natively, so
            # the newest partial can be cut off (G1.1).
            self._discard_partials(job, started, finished)
            with capture_lock:
                tail = "".join(captured[-80:])
            self._end(job, error(job.job_id, "render_failed", format_render_failure(return_code, tail)))
            return

        with progress_lock:
            # A PNG sequence writes no segments, so any manifest is another render's.
            flushed = [] if job.output_format == "png_sequence" else progress.flush_manifest()
            incomplete = list(progress.incomplete)
        for index, path in flushed:
            if not self._still_current(job):
                return
            self._report_beat(job, index, path)
        if incomplete:
            # Manim reused or wrote a clip that is cut off, so its output is
            # suspect. Delete exactly those clips, which may sit in another
            # quality folder, so the next render redoes their beats.
            root = media_dir.resolve()
            left = [
                path for path in incomplete if not path.resolve().is_relative_to(root) or not self._remove_clip(path)
            ]
            if left:
                message = f"manim used an incomplete clip {left[0]} that could not be removed"
            else:
                message = f"manim used an incomplete clip {incomplete[0].name}; it was removed, so render again"
            self._end(job, error(job.job_id, "render_failed", message))
            return
        output = progress.output_path
        if output is None or not _artifact_ready(output):
            output = self._find_output(job)
        if output is None or not _artifact_ready(output):
            self._end(job, error(job.job_id, "render_failed", "manim exited 0 without a movie"))
            return
        if job.output_format == "png_sequence":
            problem = _frame_count_problem(output, progress.frame_count)
            if problem is not None:
                self._end(job, error(job.job_id, "render_failed", problem))
                return
        if not self._still_current(job):
            return
        self._finish_output(job, output, progress.beats)

    def _report_beat(self, job: _Job, index: int, path: Path) -> None:
        """Record a finished preview beat and send ``beat_ready``. Exports send none (D17)."""
        if job.profile != "preview":
            return
        self._note_partial(job, index, path)
        self.emit({"event": "beat_ready", "jobId": job.job_id, "beatIndex": index, "path": str(path)})

    def _prepare_job_locked(self, job: _Job) -> bool:
        """Write the scene and argv. Return False when the job has ended without Manim."""
        job.project_dir = self.project
        job.scene_path = self.scene_path
        job.media_dir = self.media_dir
        job.run_marker = self.run_marker
        job.generation = self._generation
        if self.session is None:
            return self._prepare_handwritten_locked(job)
        try:
            # The start never writes project.json: every edit has saved it, and
            # a file changed outside Mathimator must not be overwritten (D59).
            self._check_disk_locked()
        except DocumentError as exc:
            self._end(job, _refusal(job.job_id, exc))
            return False
        make_folders(self.project, self.media_dir)
        project = self.session.project
        export_format = "mp4"
        export_transparent = False
        if job.profile == "export":
            export_format = project.export_profile.format
            export_transparent = project.export_profile.transparent
            existing = self._existing_export_locked(project)
            if existing is not None:
                self.log("export cache hit")
                self._end(job, {"event": "done", "jobId": job.job_id, "path": str(existing), "current": True})
                return False
        try:
            source = generate(project)
        except CodegenError as exc:
            self._end(job, rejected(job.job_id, "invalid_document", str(exc)))
            return False
        # A shared project folder could carry a planted codegen/__pycache__ or a
        # module that shadows one Manim imports. Manim imports scene.py from
        # this folder, so it holds nothing but the scene just written (F170).
        codegen = self.scene_path.parent
        try:
            _remove_tree(codegen)
            make_folders(self.project, codegen)
            self.scene_path.write_text(source, encoding="utf-8", newline="\n")
        except OSError as exc:
            self._end(job, error(job.job_id, "render_failed", f"could not write {self.scene_path}: {exc}"))
            return False
        job.captured = load_text(dumps_project(project))
        job.capture_stamp = scene_stamp(project)
        job.capture_keys = beat_cache_keys(project, job.profile)
        if job.profile == "preview":
            width = project.preview_profile.width
            height = project.preview_profile.height
            fps = project.preview_profile.fps
            self._rebuild_index_locked()
            self._copy_last_good_locked()
            self._save_sidecars_locked()
        else:
            width = project.export_profile.width
            height = project.export_profile.height
            fps = project.export_profile.fps
        job.quality = f"{height}p{fps}"
        job.argv = build_manim_argv(
            self.python,
            self.scene_path,
            self.media_dir,
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
        if not self.scene_path.is_file():
            message = f"No project is open, and {self.scene_path} does not exist."
            self._end(job, rejected(job.job_id, "no_project", message))
            return False
        make_folders(self.project, self.media_dir)
        _width, height, fps = profile_size(job.profile)
        job.quality = f"{height}p{fps}"
        job.argv = build_manim_argv(self.python, self.scene_path, self.media_dir, job.profile)
        return True

    def _note_partial(self, job: _Job, index: int, path: Path) -> None:
        with self._doc_lock:
            if not self._matches_locked(job) or self._index is None:
                return
            if index < 0 or index >= len(self._index.beats):
                return
            beat = self._index.beats[index]
            beat.partial = str(path)
            beat.fresh = True
            if self.session is not None:
                self.session.preview_index = self._index.to_dict()
            self._write_index_locked()

    def _finish_output(self, job: _Job, output: Path, partials: dict[int, Path]) -> None:
        if job.profile == "export":
            if job.captured is None:
                # Hand-written mode: Manim's own output, with no copy in exports/ and no current (C7.7).
                self._end(job, {"event": "done", "jobId": job.job_id, "path": str(output)})
                return
            with self._doc_lock:
                published = self._publish_export_locked(job, output)
            if published is None:
                self._end(job, error(job.job_id, "publish_failed", "refusing to copy a preview file into exports"))
                return
            current = False
            with self._doc_lock:
                if self.session is not None:
                    current = export_key(job.captured) == export_key(self.session.project)
            self._end(job, {"event": "done", "jobId": job.job_id, "path": str(published), "current": current})
            return

        state: dict[str, object] | None = None
        with self._doc_lock:
            if self._matches_locked(job) and self.session is not None and self._index is not None:
                for index, path in partials.items():
                    if 0 <= index < len(self._index.beats):
                        self._index.beats[index].partial = str(path)
                        self._index.beats[index].fresh = True
                self._index.output = str(output)
                self.session.mark_rendered("preview")
                self.session.preview_index = self._index.to_dict()
                self._save_sidecars_locked()
                state = self.session.state_event(job.job_id, str(self.project))
        if state is not None and self._still_current(job):
            self.emit(state)
        if self._still_current(job):
            self._end(job, {"event": "done", "jobId": job.job_id, "path": str(output)})

    def _matches_locked(self, job: _Job) -> bool:
        if self.session is None or job.capture_stamp is None or job.capture_keys is None:
            return False
        # A job of a folder bound before never records into this one's index.
        if job.profile != "preview" or job.generation != self._generation:
            return False
        return job.capture_stamp == scene_stamp(self.session.project) and job.capture_keys == beat_cache_keys(
            self.session.project, "preview"
        )

    def _publish_export_locked(self, job: _Job, output: Path) -> Path | None:
        captured = job.captured
        project_dir = job.project_dir
        media_dir = job.media_dir
        if captured is None or project_dir is None or media_dir is None:
            return None
        # Judge the path below the media folder only, so a user folder named
        # 480p15 above the project does not look like the preview (G4.9).
        try:
            parts = output.resolve().relative_to(media_dir.resolve()).parts
        except ValueError:
            return None
        fmt = captured.export_profile.format
        file_name = export_artifact_name(fmt)
        png_sequence = fmt == "png_sequence"
        if png_sequence:
            if not output.is_dir() or not any(output.glob("*.png")):
                return None
            # A preview movie lives under 480p15. PNG frames do not, unless a
            # caller hands that folder over by mistake.
            if "480p15" in parts:
                return None
        else:
            if job.quality and job.quality not in parts:
                return None
            if "480p15" in parts and job.quality != "480p15":
                return None
            if not output.is_file():
                return None
            expected = {".mp4": "mp4", ".gif": "gif", ".webm": "webm"}.get(output.suffix.lower())
            if expected != fmt:
                return None
        folder = project_dir / ".mathimator" / "exports" / export_folder_name(captured)
        make_folders(project_dir, folder)
        if png_sequence:
            target = folder / file_name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(output, target)
        else:
            target = folder / file_name
            if output.resolve() == target.resolve():
                return None
            shutil.copyfile(output, target)
        manifest = {
            "exportKey": export_key(captured),
            "manimVersion": MANIM_VERSION,
            "generatorVersion": captured.generator_version,
            "sceneHash": scene_hash(captured),
            "file": file_name,
            "profile": {
                "format": captured.export_profile.format,
                "width": captured.export_profile.width,
                "height": captured.export_profile.height,
                "fps": captured.export_profile.fps,
                "transparent": captured.export_profile.transparent,
            },
        }
        (folder / "export.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return target

    def _existing_export_locked(self, project: Project) -> Path | None:
        folder = self.project / ".mathimator" / "exports" / export_folder_name(project)
        manifest_path = folder / "export.json"
        artifact = folder / export_artifact_name(project.export_profile.format)
        if project.export_profile.format == "png_sequence":
            ready = artifact.is_dir() and any(artifact.glob("*.png"))
        else:
            ready = artifact.is_file()
        if not ready or not manifest_path.is_file():
            return None
        manifest = _read_record(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("exportKey") != export_key(project):
            return None
        return artifact

    def _copy_last_good_locked(self) -> None:
        if self._index is None:
            return
        make_folders(self.project, self.last_good_dir)
        for beat in self._index.beats:
            if beat.fresh:
                continue
            source_text = beat.last_good or beat.partial
            if not source_text:
                continue
            source = Path(source_text)
            if not source.is_file():
                continue
            dest = self.last_good_dir / f"{beat.id}.mp4"
            if source.resolve() != dest.resolve():
                shutil.copyfile(source, dest)
            beat.last_good = str(dest)
            beat.partial = None
            beat.fresh = False
        if self.session is not None:
            self.session.preview_index = self._index.to_dict()

    def _rebuild_index_locked(self) -> None:
        if self.session is None:
            self._index = None
            return
        previous = {beat.id: beat for beat in self._index.beats} if self._index is not None else {}
        output = self._index.output if self._index is not None else None
        dirty = self.session.dirty_flags("preview")
        durations = [beat.duration for beat in self.session.project.scene.beats]
        starts = derived_starts(durations)
        beats: list[IndexBeat] = []
        for index, beat in enumerate(self.session.project.scene.beats):
            prior = previous.get(beat.id)
            if dirty[index]:
                candidate = prior.last_good or prior.partial if prior is not None else None
                last_good = candidate if candidate and self._clips.complete(Path(candidate)) else None
                beats.append(
                    IndexBeat(
                        id=beat.id,
                        index=index,
                        derived_start=float(starts[index]),
                        duration=float(beat.duration),
                        partial=None,
                        last_good=last_good,
                        fresh=False,
                    )
                )
                continue
            # Only a complete clip makes its beat fresh (C14 Q3).
            candidate = prior.partial or prior.last_good if prior is not None else None
            partial = candidate if candidate and self._clips.complete(Path(candidate)) else None
            beats.append(
                IndexBeat(
                    id=beat.id,
                    index=index,
                    derived_start=float(starts[index]),
                    duration=float(beat.duration),
                    partial=partial,
                    last_good=None,
                    fresh=partial is not None,
                )
            )
        if any(dirty) or (output is not None and not self._clips.complete(Path(output))):
            output = None
        self._index = PreviewIndex(output=output, beats=beats)
        self.session.preview_index = self._index.to_dict()

    def _check_root_locked(self) -> _Binding:
        """Check 1 of C8.1: the bound folder is still there, with the identity it was bound with (D57)."""
        binding = self._binding
        if binding is None or folder_identity(self.project) != binding.identity:
            raise DocumentError(PROJECT_UNAVAILABLE, code="project_unavailable")
        return binding

    def _check_file_locked(self) -> _Binding:
        """Checks 1 and 2 of C8.1: the folder, and a project.json in it."""
        binding = self._check_root_locked()
        if not self.project_file.exists():
            raise DocumentError(PROJECT_UNAVAILABLE, code="project_unavailable")
        return binding

    def _read_disk_locked(self) -> tuple[_Binding, bytes]:
        """Checks 1 to 3 of C8.1: the folder, then the bytes of its project.json (G5.9)."""
        binding = self._check_file_locked()
        return binding, _read_project_bytes(self.project_file)

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
            write_atomically(self.project_file, data)
        except OSError as exc:
            raise _save_failed(exc) from exc
        binding.fingerprint = fingerprint(data)

    def _save_sidecars_locked(self) -> None:
        if self.session is None:
            return
        self._write_record(self.rendered_path, self.session.rendered_payload())
        self._write_index_locked()

    def _write_index_locked(self) -> None:
        if self._index is None:
            return
        self._write_record(self.index_path, self._index.stored(self.project))

    def _write_record(self, path: Path, value: object) -> None:
        """Write one cache record. A record that cannot be written is logged, not fatal (C8.1)."""
        try:
            make_folders(self.project, path.parent)
            path.write_text(_record_text(value), encoding="utf-8", newline="\n")
        except OSError as exc:
            self.log(f"could not write {path}: {exc}")

    def _read_rendered(self) -> object:
        return _read_record(self.rendered_path)

    def _read_index(self) -> PreviewIndex | None:
        """The stored index, keeping only paths inside this project's media and last-good folders (D18, F035)."""
        return load_index(_read_record(self.index_path), self.project, (self.media_dir, self.last_good_dir))

    def _find_output(self, job: _Job) -> Path | None:
        if job.scene_path is None or job.media_dir is None or not job.quality:
            return None
        return find_export_output(job.media_dir, job.scene_path.stem, job.quality, job.output_format)


def find_export_output(media_dir: Path, scene_stem: str, quality: str, fmt: str) -> Path | None:
    """Locate the primary artifact Manim wrote for one export format.

    GIF files are named ``MainScene_ManimCE_v<version>.gif``. MP4 and WebM use
    ``MainScene.<ext>``. PNG sequence is the ``images/<stem>/MainScene`` directory.
    A preview MP4 in ``480p15`` is not returned for another format.
    """
    if fmt == "png_sequence":
        images = png_frames_dir(media_dir, scene_stem)
        if images.is_dir() and any(images.glob("*.png")):
            return images
        return None
    extensions = {"mp4": ".mp4", "gif": ".gif", "webm": ".webm"}
    extension = extensions.get(fmt)
    if extension is None:
        return None
    folder = media_dir / "videos" / scene_stem / quality
    if not folder.is_dir():
        return None
    exact = folder / f"MainScene{extension}"
    if exact.is_file():
        return exact
    matches = [path for path in folder.glob(f"MainScene*{extension}") if path.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    return matches[0]


def png_frames_dir(media_dir: Path, scene_stem: str) -> Path:
    """Manim writes every PNG sequence here, whatever its size or frame rate."""
    return media_dir / "images" / scene_stem / "MainScene"


def partial_movie_dir(media_dir: Path, scene_stem: str, quality: str) -> Path:
    return media_dir / "videos" / scene_stem / quality / "partial_movie_files" / "MainScene"


def _segments(folder: Path) -> list[Path]:
    """The partial movies in ``folder``, which may not exist."""
    if not folder.is_dir():
        return []
    return [path for path in folder.iterdir() if path.suffix.lower() in SEGMENT_SUFFIXES and path.is_file()]


def _frame_count_problem(frames: Path, reported: int | None) -> str | None:
    """Describe why a PNG sequence cannot be published, or return None (F021)."""
    written = sum(1 for _ in frames.glob("*.png"))
    if reported is None:
        return f"manim did not report how many PNG frames it wrote to {frames}"
    if written != reported:
        return f"manim reported {reported} PNG frames, but {frames} holds {written}"
    return None


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


def _record_text(value: object) -> str:
    return json.dumps(value, indent=2) + "\n"


def _refusal(job_id: str, exc: DocumentError) -> dict[str, object]:
    return rejected(job_id, exc.code, str(exc), exc.field)


def _read_record(path: Path) -> object:
    """The JSON in a cache record, or None when it is missing or unreadable, which rebuilds the record."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


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


def _artifact_ready(path: Path) -> bool:
    if path.is_file():
        return True
    if path.is_dir():
        return any(path.glob("*.png"))
    return False
