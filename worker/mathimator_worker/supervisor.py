"""Long-lived stdio supervisor. One Manim grandchild at a time.

Commands are ``status``, ``render_preview``, ``export``, ``cancel``,
``get_state``, ``edit``, ``undo``, ``redo``, ``scrub``, ``code``, and
``open_project``. Events are ``ready``, ``beat_ready``, ``done``, ``error``,
``state``, ``rejected``, ``scrub``, and ``code``. Each message is one UTF-8
JSON object on its own line.

This process does not import Manim or PyAV. A new preview cancels an in-flight
preview. An export cancels an in-flight preview and then runs. While an export
runs, the newest preview request waits. Document edits never cancel an export
and never start one. Scrub does not start Manim.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from mathimator_worker.argv import build_manim_argv
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
    load_text,
    scene_hash,
    scene_stamp,
)
from mathimator_worker.document_error import DocumentError
from mathimator_worker.process_group import terminate_process_group, warm_process_table
from mathimator_worker.progress import ManimProgress, discard_unfinished_partials
from mathimator_worker.scrub import IndexBeat, PreviewIndex, scrub_at
from mathimator_worker.session import EditResult, Session
from mathimator_worker.tex_failure import format_render_failure
from mathimator_worker.win32_job import assign_kill_on_close, close_job, jobs_enabled, terminate_job


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
    argv: list[str] = field(default_factory=list)
    quality: str = ""
    captured: Project | None = None
    capture_stamp: str | None = None
    capture_keys: list[str] | None = None


class Supervisor:
    def __init__(
        self,
        project: Path,
        python: str,
        log_path: Path | None,
        init: bool,
    ) -> None:
        self.python = python
        self.init = init
        self.project = project
        self._log_override = log_path
        self._log_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._doc_lock = threading.Lock()
        self._stdout_lock = threading.Lock()
        self._current: _Job | None = None
        self._pending_preview: str | None = None
        self.session: Session | None = None
        self._index: PreviewIndex | None = None
        self._startup_error: str | None = None
        self._bind_paths()
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_initial()
        self._warm_process_control()

    def _bind_paths(self) -> None:
        root = self.project / ".mathimator"
        self.scene_path = root / "codegen" / "scene.py"
        self.media_dir = root / "cache" / "media"
        self.last_good_dir = root / "cache" / "last-good"
        self.rendered_path = root / "cache" / "rendered.json"
        self.index_path = root / "cache" / "preview-index.json"
        self.project_file = self.project / "project.json"
        if self._log_override is not None:
            self.log_path = self._log_override
        else:
            self.log_path = root / "logs" / "worker.log"

    def _warm_process_control(self) -> None:
        if sys.platform != "win32":
            return
        try:
            warm_process_table()
        except OSError as exc:
            self.log(f"process table warmup failed: {exc}")

    def log(self, message: str) -> None:
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        line = f"{stamp} {message}\n"
        with self._log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def emit(self, event: dict[str, object]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        with self._stdout_lock:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
        self.log(f"event {payload}")

    def serve(self) -> None:
        self.log(f"supervisor listening project={self.project} python={self.python}")
        try:
            for line in sys.stdin:
                self.handle_line(line)
        finally:
            self._cancel_current()

    def handle_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            self.emit({"event": "error", "jobId": None, "message": "invalid JSON"})
            return
        if not isinstance(message, dict) or not isinstance(message.get("cmd"), str):
            self.emit({"event": "error", "jobId": None, "message": "missing cmd"})
            return
        command = message["cmd"]
        request_id = message.get("id")
        job_id = request_id if isinstance(request_id, str) and request_id else command
        self.log(f"command {text}")
        if command == "status":
            self.emit({"event": "ready", "jobId": job_id})
            return
        if command == "render_preview":
            self._request_preview(job_id)
            return
        if command == "export":
            self._request_export(job_id)
            return
        if command == "cancel":
            target = message.get("jobId")
            target_id = target if isinstance(target, str) else None
            self._cancel(target_id)
            return
        if command == "get_state":
            self._emit_state(job_id)
            return
        if command == "edit":
            self._edit(job_id, message.get("edit"))
            return
        if command == "undo":
            self._history(job_id, undo=True)
            return
        if command == "redo":
            self._history(job_id, undo=False)
            return
        if command == "scrub":
            self._scrub(job_id, message.get("time"))
            return
        if command == "code":
            self._code(job_id)
            return
        if command == "clear_cache":
            self._clear_cache(job_id)
            return
        if command == "open_project":
            raw_path = message.get("path")
            if not isinstance(raw_path, str) or raw_path == "":
                self._reject(job_id, "open_project needs a path")
                return
            self._open_project(job_id, Path(raw_path))
            return
        self.emit(
            {
                "event": "error",
                "jobId": job_id,
                "message": f"unknown command {command}",
            }
        )

    def _load_initial(self) -> None:
        if self.project_file.is_file():
            try:
                project = load_text(self.project_file.read_text(encoding="utf-8"))
            except (OSError, DocumentError) as exc:
                self.session = None
                self._index = None
                self._startup_error = str(exc)
                self.log(f"project refused: {exc}")
                return
            self._adopt(project)
            return
        if self.init:
            self.session = Session(empty_project())
            self._index = PreviewIndex(output=None, beats=[])
            with self._doc_lock:
                self._save_document_locked()
                self._rebuild_index_locked()
                self._save_sidecars_locked()
            return
        self.session = None
        self._index = None

    def _adopt(self, project: Project) -> None:
        """Caller holds ``_doc_lock``, or startup has not yet started threads."""
        self.session = Session(project)
        self._index = self._read_index()
        self.session.load_rendered(self._read_rendered())
        self._rebuild_index_locked()
        self._save_sidecars_locked()

    def _emit_state(self, job_id: str) -> None:
        with self._doc_lock:
            payload = self._state_locked(job_id)
        self.emit(payload)

    def _state_locked(self, job_id: str) -> dict[str, object]:
        if self.session is None:
            message = self._startup_error or "no project is open"
            return {"event": "rejected", "jobId": job_id, "message": message}
        return self.session.state_event(job_id, str(self.project))

    def _edit(self, job_id: str, edit: object) -> None:
        if not isinstance(edit, dict):
            self._reject(job_id, "edit must be an object")
            return
        with self._doc_lock:
            if self.session is None:
                self._reject(job_id, self._startup_error or "no project is open")
                return
            try:
                result = self.session.edit(edit)
                self._rebuild_index_locked()
                self._save_document_locked()
                self._save_sidecars_locked()
                payload = self._state_locked(job_id)
            except DocumentError as exc:
                self._reject(job_id, str(exc))
                return
        self._attach_focus(payload, result)
        self.emit(payload)

    def _history(self, job_id: str, *, undo: bool) -> None:
        with self._doc_lock:
            if self.session is None:
                self._reject(job_id, self._startup_error or "no project is open")
                return
            try:
                if undo:
                    self.session.undo()
                else:
                    self.session.redo()
                self._rebuild_index_locked()
                self._save_document_locked()
                self._save_sidecars_locked()
                payload = self._state_locked(job_id)
            except DocumentError as exc:
                self._reject(job_id, str(exc))
                return
        self.emit(payload)

    def _scrub(self, job_id: str, time_value: object) -> None:
        if isinstance(time_value, bool) or not isinstance(time_value, (int, float)):
            self._reject(job_id, "scrub time must be a number")
            return
        with self._doc_lock:
            index = self._index
        self.log(f"scrub time={float(time_value)}")
        result = scrub_at(index, float(time_value))
        result["event"] = "scrub"
        result["jobId"] = job_id
        self.emit(result)

    def _code(self, job_id: str) -> None:
        with self._doc_lock:
            if self.session is None:
                self._reject(job_id, self._startup_error or "no project is open")
                return
            try:
                source = generate(self.session.project)
            except CodegenError as exc:
                self._reject(job_id, str(exc))
                return
        self.emit({"event": "code", "jobId": job_id, "source": source})

    def _open_project(self, job_id: str, path: Path) -> None:
        if path.is_file() and path.name == "project.json":
            path = path.parent
        project_file = path / "project.json"
        try:
            project = load_text(project_file.read_text(encoding="utf-8"))
        except OSError as exc:
            self._reject(job_id, f"could not read project.json: {exc.strerror or exc}")
            return
        except DocumentError as exc:
            self._reject(job_id, str(exc))
            return
        self._stop_preview_only()
        with self._doc_lock:
            self.project = path.resolve()
            self._bind_paths()
            self.media_dir.mkdir(parents=True, exist_ok=True)
            self._startup_error = None
            self._adopt(project)
            payload = self._state_locked(job_id)
        self.emit(payload)

    def _reject(self, job_id: str, message: str) -> None:
        self.emit({"event": "rejected", "jobId": job_id, "message": message})

    def _attach_focus(self, payload: dict[str, object], result: EditResult) -> None:
        if result.focus_beat_id is None and result.focus_object_id is None:
            return
        payload["focus"] = {
            "beatId": result.focus_beat_id,
            "objectId": result.focus_object_id,
        }

    def _request_preview(self, job_id: str) -> None:
        to_join: threading.Thread | None = None
        proc: subprocess.Popen[str] | None = None
        queued = False
        replaced: str | None = None
        with self._state_lock:
            current = self._current
            if current is not None and current.thread.is_alive() and current.profile == "export":
                replaced = self._pending_preview
                self._pending_preview = job_id
                queued = True
                self.log(f"preview {job_id} waiting for export")
            elif current is not None and current.thread.is_alive():
                current.cancel.set()
                proc = current.proc
                to_join = current.thread
        if queued:
            if replaced is not None:
                self._emit_cancelled(replaced)
            return
        if not self._stop_process(proc, to_join, job_id):
            return
        self._start_job(job_id, "preview")

    def _request_export(self, job_id: str) -> None:
        to_join: threading.Thread | None = None
        proc: subprocess.Popen[str] | None = None
        with self._state_lock:
            current = self._current
            if current is not None and current.thread.is_alive() and current.profile == "export":
                self.emit(
                    {
                        "event": "error",
                        "jobId": job_id,
                        "message": "export is already running",
                    }
                )
                return
            if current is not None and current.thread.is_alive():
                current.cancel.set()
                proc = current.proc
                to_join = current.thread
        if not self._stop_process(proc, to_join, job_id):
            return
        self._start_job(job_id, "export")

    def _stop_preview_only(self) -> None:
        to_join: threading.Thread | None = None
        proc: subprocess.Popen[str] | None = None
        with self._state_lock:
            current = self._current
            if current is None or not current.thread.is_alive() or current.profile != "preview":
                return
            current.cancel.set()
            proc = current.proc
            to_join = current.thread
        self._stop_process(proc, to_join, "open")

    def _stop_process(
        self,
        proc: subprocess.Popen[str] | None,
        thread: threading.Thread | None,
        job_id: str,
    ) -> bool:
        if proc is not None and proc.poll() is None:
            self.log(f"stopping in-flight preview for {job_id}")
            self._kill_process(proc, self._job_handle(proc))
        if thread is not None:
            thread.join(timeout=1)
            if thread.is_alive():
                self.emit(
                    {
                        "event": "error",
                        "jobId": job_id,
                        "message": "could not stop the in-flight render",
                    }
                )
                return False
        return True

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

    def _cancel(self, job_id: str | None) -> None:
        with self._state_lock:
            current = self._current
            if current is None or not current.thread.is_alive():
                self.emit(
                    {
                        "event": "error",
                        "jobId": job_id,
                        "message": "no matching render",
                    }
                )
                return
            if job_id is not None and job_id != current.job_id:
                self.emit(
                    {
                        "event": "error",
                        "jobId": job_id,
                        "message": "no matching render",
                    }
                )
                return
            current.cancel.set()
            proc = current.proc
        self.log(f"cancel {current.job_id}")
        if proc is not None and proc.poll() is None:
            self._kill_process(proc, current.win_job)

    def _cancel_current(self) -> None:
        with self._state_lock:
            current = self._current
            proc = current.proc if current is not None else None
            if current is not None:
                current.cancel.set()
        if proc is not None and proc.poll() is None:
            self._kill_process(proc, current.win_job if current is not None else None)

    def _job_handle(self, proc: subprocess.Popen[str]) -> int | None:
        with self._state_lock:
            current = self._current
            if current is not None and current.proc is proc:
                return current.win_job
        return None

    def _kill_process(self, proc: subprocess.Popen[str], handle: int | None) -> None:
        if proc.poll() is not None:
            return
        if handle:
            try:
                terminate_job(handle)
            except OSError as exc:
                self.log(f"job terminate failed: {exc}")
                terminate_process_group(proc)
            return
        terminate_process_group(proc)

    def _discard_partials(self, job: _Job, started: float, finished: set[Path]) -> None:
        media = job.media_dir
        if media is None:
            return
        for path in discard_unfinished_partials(media, started, finished):
            self.log(f"removed unfinished media {path}")

    def _release_job(self, job: _Job) -> None:
        handle = job.win_job
        job.win_job = None
        if handle:
            close_job(handle)

    def _clear_cache(self, job_id: str) -> None:
        with self._state_lock:
            current = self._current
            if current is not None and current.thread.is_alive():
                self._reject(job_id, "A render is in progress.")
                return
        root = self.project / ".mathimator"
        for name in ("cache", "codegen", "equation", "typst"):
            path = root / name
            if path.exists():
                shutil.rmtree(path)
        logs = root / "logs"
        if logs.is_dir():
            for child in list(logs.iterdir()):
                if child.resolve() == self.log_path.resolve():
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        self.log(f"could not remove {child}")
        with self._doc_lock:
            self.media_dir.mkdir(parents=True, exist_ok=True)
            if self.session is not None:
                self.session.rendered.clear()
                self._index = None
                self._rebuild_index_locked()
                self._save_sidecars_locked()
            payload = self._state_locked(job_id)
        self.emit(payload)

    def _run_job(self, job: _Job) -> None:
        try:
            self._run_job_inner(job)
        except Exception as exc:
            self.log(f"job {job.job_id} crashed: {exc}")
            if self._still_current(job):
                self.emit(
                    {
                        "event": "error",
                        "jobId": job.job_id,
                        "message": f"supervisor failure: {exc}",
                    }
                )
        finally:
            self._finish_job(job)

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

    def _run_job_inner(self, job: _Job) -> None:
        with self._doc_lock:
            if not self._prepare_job_locked(job):
                return
            argv = list(job.argv)
        self.log("argv: " + json.dumps(argv))
        if job.cancel.is_set() or not self._still_current(job):
            self._emit_cancelled(job.job_id)
            return
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
        started = time.time()
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
            self.emit(
                {
                    "event": "error",
                    "jobId": job.job_id,
                    "message": f"failed to start manim: {exc}",
                }
            )
            return

        handle: int | None = None
        if jobs_enabled():
            try:
                handle = assign_kill_on_close(proc.pid)
            except OSError as exc:
                self.log(f"job object assignment failed: {exc}")
            else:
                self.log(f"job assigned pid={proc.pid}")
        with self._state_lock:
            if self._current is job:
                job.proc = proc
                job.win_job = handle
            cancelled = job.cancel.is_set() or self._current is not job
        if cancelled:
            self._kill_process(proc, handle)
            self._discard_partials(job, started, set())
            self._emit_cancelled(job.job_id)
            return

        media_dir = job.media_dir
        if media_dir is None:
            self.emit(
                {
                    "event": "error",
                    "jobId": job.job_id,
                    "message": "render has no media directory",
                }
            )
            return
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
                    self._note_partial(job, index, path)
                    self.emit(
                        {
                            "event": "beat_ready",
                            "jobId": job.job_id,
                            "beatIndex": index,
                            "path": str(path),
                        }
                    )

        assert proc.stdout is not None and proc.stderr is not None
        stdout_thread = threading.Thread(target=consume, args=(proc.stdout,), daemon=True)
        stderr_thread = threading.Thread(target=consume, args=(proc.stderr,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        return_code = proc.wait()
        self._release_job(job)
        stdout_thread.join()
        stderr_thread.join()

        if not self._still_current(job):
            with progress_lock:
                finished = set(progress.beats.values())
                if progress.output_path is not None:
                    finished.add(progress.output_path)
            self._discard_partials(job, started, finished)
            self._emit_cancelled(job.job_id)
            return
        if return_code != 0:
            with capture_lock:
                tail = "".join(captured[-80:])
            self.emit(
                {
                    "event": "error",
                    "jobId": job.job_id,
                    "message": format_render_failure(return_code, tail),
                }
            )
            return

        with progress_lock:
            flushed = progress.flush_manifest()
        for index, path in flushed:
            if not self._still_current(job):
                self._emit_cancelled(job.job_id)
                return
            self._note_partial(job, index, path)
            self.emit(
                {
                    "event": "beat_ready",
                    "jobId": job.job_id,
                    "beatIndex": index,
                    "path": str(path),
                }
            )
        output = progress.output_path
        if output is None or not _artifact_ready(output):
            output = self._find_output(job)
        if output is None or not _artifact_ready(output):
            self.emit(
                {
                    "event": "error",
                    "jobId": job.job_id,
                    "message": "manim exited 0 without a movie",
                }
            )
            return
        if not self._still_current(job):
            self._emit_cancelled(job.job_id)
            return
        self._finish_output(job, output, progress.beats)

    def _prepare_job_locked(self, job: _Job) -> bool:
        """Write the scene and argv. Return False when no Manim process is needed."""
        job.project_dir = self.project
        job.scene_path = self.scene_path
        job.media_dir = self.media_dir
        self.media_dir.mkdir(parents=True, exist_ok=True)
        if self.session is None:
            if not self.scene_path.is_file():
                self.emit(
                    {
                        "event": "error",
                        "jobId": job.job_id,
                        "message": f"scene file not found: {self.scene_path}",
                    }
                )
                return False
            width, height, fps = _preset(job.profile)
            job.quality = f"{height}p{fps}"
            job.argv = build_manim_argv(self.python, self.scene_path, self.media_dir, job.profile)
            return True

        project = self.session.project
        export_format = "mp4"
        export_transparent = False
        if job.profile == "export":
            export_format = project.export_profile.format
            export_transparent = project.export_profile.transparent
            existing = self._existing_export_locked(project)
            if existing is not None:
                self.log("export cache hit")
                self.emit({"event": "done", "jobId": job.job_id, "path": str(existing), "current": True})
                return False
        self._save_document_locked()
        try:
            source = generate(project)
        except CodegenError as exc:
            self.emit({"event": "error", "jobId": job.job_id, "message": str(exc)})
            return False
        self.scene_path.parent.mkdir(parents=True, exist_ok=True)
        self.scene_path.write_text(source, encoding="utf-8", newline="\n")
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

    def _note_partial(self, job: _Job, index: int, path: Path) -> None:
        if job.profile != "preview":
            return
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
                self.emit({"event": "done", "jobId": job.job_id, "path": str(output)})
                return
            with self._doc_lock:
                published = self._publish_export_locked(job, output)
            if published is None:
                self.emit(
                    {
                        "event": "error",
                        "jobId": job.job_id,
                        "message": "refusing to copy a preview file into exports",
                    }
                )
                return
            current = False
            with self._doc_lock:
                if job.captured is not None and self.session is not None:
                    current = export_key(job.captured) == export_key(self.session.project)
            self.emit(
                {
                    "event": "done",
                    "jobId": job.job_id,
                    "path": str(published),
                    "current": current,
                }
            )
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
            self.emit({"event": "done", "jobId": job.job_id, "path": str(output)})

    def _matches_locked(self, job: _Job) -> bool:
        if self.session is None or job.capture_stamp is None or job.capture_keys is None:
            return False
        if job.profile != "preview":
            return False
        return job.capture_stamp == scene_stamp(self.session.project) and job.capture_keys == beat_cache_keys(
            self.session.project, "preview"
        )

    def _publish_export_locked(self, job: _Job, output: Path) -> Path | None:
        captured = job.captured
        project_dir = job.project_dir
        if captured is None or project_dir is None:
            return None
        fmt = captured.export_profile.format
        file_name = export_artifact_name(fmt)
        png_sequence = fmt == "png_sequence"
        if png_sequence:
            if not output.is_dir() or not any(output.glob("*.png")):
                return None
            # A preview movie lives under 480p15. PNG frames do not, unless a
            # caller hands that folder over by mistake.
            if "480p15" in output.parts:
                return None
        else:
            if job.quality and job.quality not in output.parts:
                return None
            if "480p15" in output.parts and job.quality != "480p15":
                return None
            if not output.is_file():
                return None
            expected = {".mp4": "mp4", ".gif": "gif", ".webm": "webm"}.get(output.suffix.lower())
            if expected != fmt:
                return None
        folder = project_dir / ".mathimator" / "exports" / export_folder_name(captured)
        folder.mkdir(parents=True, exist_ok=True)
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
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(manifest, dict) or manifest.get("exportKey") != export_key(project):
            return None
        return artifact

    def _copy_last_good_locked(self) -> None:
        if self._index is None:
            return
        self.last_good_dir.mkdir(parents=True, exist_ok=True)
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
                last_good = candidate if candidate and Path(candidate).is_file() else None
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
            candidate = prior.partial or prior.last_good if prior is not None else None
            partial = candidate if candidate and Path(candidate).is_file() else None
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
        if any(dirty):
            output = None
        self._index = PreviewIndex(output=output, beats=beats)
        self.session.preview_index = self._index.to_dict()

    def _save_document_locked(self) -> None:
        if self.session is None:
            return
        payload = dumps_project(self.session.project)
        self.project_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.project_file.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temporary, self.project_file)

    def _save_sidecars_locked(self) -> None:
        if self.session is None:
            return
        self.rendered_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(self.session.rendered_payload(), indent=2) + "\n"
        self.rendered_path.write_text(rendered, encoding="utf-8", newline="\n")
        self._write_index_locked()

    def _write_index_locked(self) -> None:
        if self._index is None:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(self._index.to_dict(), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _read_rendered(self) -> object:
        if not self.rendered_path.is_file():
            return None
        try:
            return json.loads(self.rendered_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def _read_index(self) -> PreviewIndex | None:
        if not self.index_path.is_file():
            return None
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("beats"), list):
            return None
        beats: list[IndexBeat] = []
        for item in raw["beats"]:
            if not isinstance(item, dict):
                continue
            beat_id = item.get("id")
            index = item.get("index")
            if not isinstance(beat_id, str) or not isinstance(index, int):
                continue
            beats.append(
                IndexBeat(
                    id=beat_id,
                    index=index,
                    derived_start=_as_float(item.get("derivedStart")),
                    duration=_as_float(item.get("duration")),
                    partial=item.get("partial") if isinstance(item.get("partial"), str) else None,
                    last_good=item.get("lastGood") if isinstance(item.get("lastGood"), str) else None,
                    fresh=item.get("fresh") is True,
                )
            )
        output = raw.get("output")
        return PreviewIndex(output=output if isinstance(output, str) else None, beats=beats)

    def _find_output(self, job: _Job) -> Path | None:
        if job.scene_path is None or job.media_dir is None or not job.quality:
            return None
        fmt = "mp4"
        if job.profile == "export" and job.captured is not None:
            fmt = job.captured.export_profile.format
        return find_export_output(job.media_dir, job.scene_path.stem, job.quality, fmt)

    def _emit_cancelled(self, job_id: str) -> None:
        self.emit({"event": "error", "jobId": job_id, "message": "cancelled"})


def find_export_output(media_dir: Path, scene_stem: str, quality: str, fmt: str) -> Path | None:
    """Locate the primary artifact Manim wrote for one export format.

    GIF files are named ``MainScene_ManimCE_v<version>.gif``. MP4 and WebM use
    ``MainScene.<ext>``. PNG sequence is the ``images/<stem>/MainScene`` directory.
    A preview MP4 in ``480p15`` is not returned for another format.
    """
    if fmt == "png_sequence":
        images = media_dir / "images" / scene_stem / "MainScene"
        if "480p15" in images.parts:
            return None
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


def _artifact_ready(path: Path) -> bool:
    if path.is_file():
        return True
    if path.is_dir():
        return any(path.glob("*.png"))
    return False


def _preset(profile: str) -> tuple[int, int, int]:
    if profile == "preview":
        return 854, 480, 15
    if profile == "export":
        return 1920, 1080, 30
    raise ValueError(f"unknown profile {profile}")


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
