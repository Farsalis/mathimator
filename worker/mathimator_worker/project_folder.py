"""The project folder on disk: its lock, its identity, and safe writes (contract C8.1).

A worker holds an exclusive OS lock on ``<project>/.mathimator/lock`` while it
is bound to the folder (D58). The lock, not the file, means "in use", and the
OS drops it when the process dies. The folder's ``(st_dev, st_ino)`` tells a
moved or replaced folder from the one that was bound (D57). project.json is
replaced through a unique temporary file in the same folder, synced before the
rename (C13.2 E3), and a finished export is published the same way (C7.2).
Nothing here creates a project folder: folders are made only inside one that
exists. A delete below the project folder removes a symbolic link or a
junction, and never goes through one (O10).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import stat
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    # Windows reports a lock held by another process as EACCES.
    _HELD = PermissionError
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    _HELD = BlockingIOError

# Waits between os.replace attempts while Windows reports the target in use: about 0.5 s in all.
_REPLACE_WAITS = (0.02, 0.05, 0.08, 0.15, 0.2)
_IN_USE_ERRORS = frozenset({5, 32})

# shutil.rmtree's onexc: the function that failed, the path it failed on, and the exception.
OnExc = Callable[[Callable[..., object], str, BaseException], object]


class ProjectLocked(OSError):
    """Another process holds the project lock."""


class LinkedFolder(OSError):
    """A folder below the project folder is a symbolic link or a junction, so nothing is deleted through it (O10)."""

    def __init__(self, link: Path) -> None:
        super().__init__(f"{link} is a link to another folder")


class ProjectLock:
    """The exclusive lock on one project folder (D58).

    ``created`` is what taking the lock created: the ``.mathimator`` folder, or
    the lock file inside one that existed, or None.
    """

    def __init__(self, fd: int, created: Path | None) -> None:
        self._fd: int | None = fd
        self.created = created

    @classmethod
    def acquire(cls, folder: Path) -> ProjectLock:
        """Lock ``folder``. Raises ProjectLocked or another OSError, having removed what it created."""
        state = folder / ".mathimator"
        path = state / "lock"
        created: Path | None = None
        try:
            with suppress(FileExistsError):
                state.mkdir()
                created = state
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                created = created or path
            except FileExistsError:
                fd = os.open(path, os.O_RDWR)
        except OSError:
            remove_quietly(created)
            raise
        try:
            _lock(fd)
        except OSError as exc:
            os.close(fd)
            remove_quietly(created)
            if isinstance(exc, _HELD):
                raise ProjectLocked("it is open in another Mathimator window") from exc
            raise
        return cls(fd, created)

    def release(self) -> None:
        """Release the lock. Releasing it again does nothing."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with suppress(OSError):
            _unlock(fd)
        with suppress(OSError):
            os.close(fd)

    def discard(self) -> None:
        """Release the lock and remove what taking it created."""
        self.release()
        remove_quietly(self.created)


def folder_identity(path: Path) -> tuple[int, int] | None:
    """The ``(st_dev, st_ino)`` of the folder at ``path``, or None when no folder is there.

    On Windows these are the volume serial number and the file id.
    """
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    return info.st_dev, info.st_ino


def make_folders(root: Path, folder: Path) -> None:
    """Create ``folder`` and the folders between it and ``root``. ``root`` itself is never created (D57)."""
    if folder.is_dir():
        return
    current = root
    for part in folder.relative_to(root).parts:
        current /= part
        current.mkdir(exist_ok=True)


def fingerprint(data: bytes) -> str:
    """The disk fingerprint of project.json: the SHA-256 of its exact bytes (D59)."""
    return hashlib.sha256(data).hexdigest()


def write_atomically(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` through a synced temporary file in the same folder.

    On failure the temporary file is gone, ``path`` is as it was, and OSError is raised.
    """
    temporary = _temporary(path, "tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            temporary.unlink()
        raise


def copy_atomically(source: Path, target: Path) -> None:
    """Replace ``target`` with a copy of ``source``, a file or a folder, through a temporary copy beside it.

    A folder cannot be replaced in one step, so the old one is moved aside
    first and moved back when the copy cannot take its place. On failure the
    temporary copy is gone, ``target`` is as it was, and OSError is raised.
    """
    temporary = _temporary(target, "tmp")
    try:
        if not source.is_dir():
            shutil.copyfile(source, temporary)
            replace(temporary, target)
            return
        shutil.copytree(source, temporary)
        aside = _temporary(target, "old")
        if target.exists():
            replace(target, aside)
        try:
            replace(temporary, target)
        except BaseException:
            if aside.exists():
                replace(aside, target)
            raise
        remove_quietly(aside)
    except BaseException:
        remove_quietly(temporary)
        raise


def replace(source: Path, target: Path) -> None:
    """``os.replace``, retried for about 0.5 s while Windows reports a file in use (errors 5 and 32, G5.9)."""
    _retry_in_use(lambda: os.replace(source, target))


def remove_file(path: Path) -> None:
    """Delete a file that may not exist, retried for about 0.5 s while Windows reports it in use (G5.10)."""
    _retry_in_use(lambda: path.unlink(missing_ok=True))


def _retry_in_use(action: Callable[[], None]) -> None:
    for wait in (*_REPLACE_WAITS, None):
        try:
            action()
            return
        except PermissionError as exc:
            if wait is None or getattr(exc, "winerror", None) not in _IN_USE_ERRORS:
                raise
            time.sleep(wait)


def _temporary(path: Path, kind: str) -> Path:
    """A unique hidden name beside ``path``."""
    return path.with_name(f".{path.name}.{secrets.token_hex(8)}.{kind}")


def is_link(path: Path) -> bool:
    """Whether ``path`` is a symbolic link or a junction."""
    return path.is_symlink() or path.is_junction()


def first_link(root: Path, path: Path) -> Path | None:
    """The first folder from below ``root`` down to ``path``, ``path`` included, that is a link, or None (O10).

    ``root`` is the project folder, resolved when it was bound, so a link above it is the user's own.
    """
    folder = root
    for part in path.relative_to(root).parts:
        folder /= part
        if is_link(folder):
            return folder
    return None


def link_refusal(link: Path) -> str:
    """The message of ``linked_folder``: ``link``, one of the project's own files or folders, is a link (O10)."""
    return (
        f"Mathimator cannot use {link}: it is a link to another location, and Mathimator keeps its files "
        "inside the project. Delete the link and try again."
    )


def remove_below(root: Path, path: Path, onexc: OnExc) -> None:
    """Delete ``path`` below ``root``: a folder with all it holds, a file or a link, never through a link (O10).

    A link goes, never what it points to, whether it is ``path`` or inside it,
    where shutil.rmtree removes links without following them. While a folder
    between ``root`` and ``path`` is a link, nothing is deleted. Each failure
    goes to ``onexc``, as in shutil.rmtree, and that one as LinkedFolder.
    """
    link = first_link(root, path.parent)
    if link is not None:
        onexc(first_link, str(path), LinkedFolder(link))
    elif path.is_dir() and not is_link(path):
        shutil.rmtree(path, onexc=onexc)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            onexc(os.unlink, str(path), exc)


def retry_read_only(function: Callable[..., object], path: str, exc: BaseException) -> None:
    """An ``onexc`` for ``remove_below``: make a read-only file or folder writable, then remove it again (S26).

    Windows will not delete either while it is read-only, as a copied or
    shared project folder can carry them. A link is never changed, and any
    other failure, including one of a call that lists or opens a folder, is
    raised again.
    """
    if function not in (os.unlink, os.rmdir) or not isinstance(exc, PermissionError):
        raise exc
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        raise exc from None
    if mode & stat.S_IWRITE or stat.S_ISLNK(mode) or os.path.isjunction(path):
        raise exc
    os.chmod(path, stat.S_IMODE(mode) | stat.S_IWRITE)
    function(path)


def remove_quietly(path: Path | None) -> None:
    """Delete a file or a folder tree that may not exist, ignoring what cannot be removed."""
    if path is None:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)


def os_reason(exc: OSError) -> str:
    """The OS's own words for ``exc``."""
    return exc.strerror or str(exc)
