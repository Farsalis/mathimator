"""Follow a Manim run through the files it writes, never through its log.

Manim writes one partial movie per beat into the ``partial_movie_files/MainScene``
folder of the job's quality, one at a time (``--max-inflight-encoders 1``), and
reuses a partial that already exists instead of writing it again. Before Manim
starts, ``ManimProgress`` records the size and mtime of every file in that
quality folder and in its partials folder (``run_files``). A file that is new
or changed since then is one this run wrote, so the comparison needs no clock
and leaves other runs' files alone. A partial counts as
finished once one with a later mtime exists, and only when its container is
complete (``clip_check``). When Manim exits, the segment list it wrote,
``partial_movie_file_list.txt``, gives the full beat order, including the
partials it reused from its cache. The run marker keeps the same record, so a
bind after a crash discards what the cut-off run wrote by the same comparison
(``discard_written``).
"""

from __future__ import annotations

import stat
from collections import Counter, deque
from pathlib import Path, PurePosixPath

from mathimator_worker.clip_check import SEGMENT_SUFFIXES, ClipChecker
from mathimator_worker.project_folder import first_link, remove_file

MANIFEST = "partial_movie_file_list.txt"
# The folder of a quality folder that Manim writes the partial movies of MainScene into.
_PARTIALS = Path("partial_movie_files", "MainScene")

# A file's size and mtime in nanoseconds.
Stamp = tuple[int, int]
# The files a discard removed, and those it could not remove, each with its error.
Discarded = tuple[list[Path], list[tuple[Path, OSError]]]


class ManimProgress:
    """One Manim run of the project in ``root``, followed through its quality folder ``video_dir``.

    ``ready`` are the beats whose partial is current already, so Manim reuses
    it, and ``waiting`` are the beats Manim renders, in order. ``waiting`` is
    None when they are not known (hand-written mode); the beats then wait for
    the segment list. Beats are handed out once each, in increasing order: a
    beat whose predecessors are not ready yet waits for them.
    """

    def __init__(
        self, root: Path, video_dir: Path, clips: ClipChecker, ready: dict[int, Path], waiting: list[int] | None
    ) -> None:
        self._root = root
        self._video_dir = video_dir
        self._partials = video_dir / _PARTIALS
        self._clips = clips
        self._before = run_files(video_dir)
        self._ready = dict(ready)
        self._waiting = None if waiting is None else deque(waiting)
        self._taken: set[Path] = set()
        self._sent = 0

    def poll(self) -> list[tuple[int, Path]]:
        """The beats that are ready while Manim runs, in increasing order.

        Each finished partial belongs to the next beat Manim renders. A
        finished partial that is not complete, one more than the beats Manim
        renders, or one that shares its mtime with another, means that order
        no longer holds or is not known, so the remaining beats wait for the
        segment list. The names never give the order.
        """
        if self._waiting is not None:
            stamps = file_stamps(self._partials, "*")
            written = [path for path in written_since(self._before, stamps) if path.suffix.lower() in SEGMENT_SUFFIXES]
            mtimes = {path: stamps[path][1] for path in written}
            # Every partial as new as the newest may still be being written.
            newest = max(mtimes.values(), default=0)
            finished = [path for path in written if mtimes[path] < newest and path not in self._taken]
            ticks = Counter(mtimes[path] for path in finished)
            for path in finished:
                self._taken.add(path)
                # Partials that finished in one clock tick have no known order.
                if not self._waiting or ticks[mtimes[path]] > 1 or not self._clips.complete(path):
                    self._waiting = None
                    break
                self._ready[self._waiting.popleft()] = path
        return self._take()

    def segments(self) -> list[Path]:
        """The partials in the segment list this run wrote, in beat order, or none when it wrote none.

        Only the file names are read from the list, and each is spelled inside
        this run's folder, so no path in an event is one Manim wrote.
        """
        manifest = self._partials / MANIFEST
        if manifest not in self._written(self._partials, MANIFEST):
            return []
        return [self._partials / name for name in read_manifest(manifest)]

    def finish(self, segments: list[Path]) -> list[tuple[int, Path]]:
        """At a clean exit: the beats not handed out yet, taken from the segment list, which holds the full order."""
        self._ready.update(enumerate(segments))
        return self._take()

    def snapshot(self) -> dict[str, list[int]]:
        """What the quality folder held before Manim started, as the run marker keeps it (``stamps_record``)."""
        return stamps_record(self._video_dir, self._before)

    def discard(self) -> Discarded:
        """Delete what this run wrote that may be cut off, after a cancel or a failure (``discard_written``)."""
        return discard_written(self._root, self._video_dir, self._before, self._clips)

    def _written(self, folder: Path, pattern: str) -> list[Path]:
        return written_since(self._before, file_stamps(folder, pattern))

    def _take(self) -> list[tuple[int, Path]]:
        """The ready beats from the next one to hand out, up to the first that is not ready."""
        beats: list[tuple[int, Path]] = []
        while self._sent in self._ready:
            beats.append((self._sent, self._ready[self._sent]))
            self._sent += 1
        return beats


def file_stamps(folder: Path, pattern: str) -> dict[Path, Stamp]:
    """Each file below ``folder`` that matches ``pattern``, with its size and mtime. A missing folder has none."""
    stamps: dict[Path, Stamp] = {}
    for path in folder.glob(pattern):
        try:
            info = path.stat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            stamps[path] = (info.st_size, info.st_mtime_ns)
    return stamps


def written_since(before: dict[Path, Stamp], now: dict[Path, Stamp]) -> list[Path]:
    """The files in ``now`` that ``before`` lacks or holds with another stamp, oldest first.

    Manim writes one file after another, so their mtimes give the order it wrote them in.
    """
    written = [path for path, stamp in now.items() if before.get(path) != stamp]
    return sorted(written, key=lambda path: (now[path][1], path.name))


def run_files(video_dir: Path) -> dict[Path, Stamp]:
    """The files in the quality folder ``video_dir`` and in its partials folder, the two a run writes into."""
    return file_stamps(video_dir, "*") | file_stamps(video_dir / _PARTIALS, "*")


def discard_written(root: Path, video_dir: Path, before: dict[Path, Stamp], clips: ClipChecker) -> Discarded:
    """Delete what an unfinished run wrote into its quality folder ``video_dir`` that may be cut off.

    The run wrote the files that ``before``, what ``run_files`` listed when it
    started, lacks or holds with another stamp. What may be cut off is an
    incomplete partial, which only the newest one can be, the segment list
    and the scene movie. Complete partials stay, so the next render reuses
    them. Nothing is deleted through a link below the project folder
    ``root``, so a link planted in the project cannot point a discard outside
    it. Return the files removed and those that could not be.
    """
    removed: list[Path] = []
    failed: list[tuple[Path, OSError]] = []
    for path in written_since(before, run_files(video_dir)):
        if first_link(root, path.parent) is not None or not _cut_off(path, video_dir, clips):
            continue
        try:
            remove_file(path)
        except OSError as exc:
            failed.append((path, exc))
        else:
            removed.append(path)
    return removed, failed


def _cut_off(path: Path, video_dir: Path, clips: ClipChecker) -> bool:
    if path.parent == video_dir / _PARTIALS:
        if path.name == MANIFEST:
            return True
        return path.suffix.lower() in SEGMENT_SUFFIXES and not clips.complete(path)
    return path.parent == video_dir and path.name.startswith("MainScene")


def stamps_record(folder: Path, stamps: dict[Path, Stamp]) -> dict[str, list[int]]:
    """``stamps`` as JSON, each path relative to ``folder`` in POSIX form, so a moved project still reads it."""
    return {path.relative_to(folder).as_posix(): list(stamp) for path, stamp in stamps.items()}


def read_stamps(folder: Path, record: object) -> dict[Path, Stamp] | None:
    """The stamps in a ``stamps_record`` of ``folder``, or None when ``record`` is not one.

    The paths are only compared with the files found in ``folder``, never opened or deleted.
    """
    if not isinstance(record, dict):
        return None
    stamps: dict[Path, Stamp] = {}
    for name, stamp in record.items():
        if not (isinstance(stamp, list) and len(stamp) == 2 and all(isinstance(part, int) for part in stamp)):
            return None
        stamps[folder / name] = (stamp[0], stamp[1])
    return stamps


def read_manifest(path: Path) -> list[str]:
    """The file names in Manim's segment list, in order.

    Each entry is ``file 'file:<posix path>'``, quoted as FFmpeg reads it, so
    an apostrophe in the path is ``'\\''`` (``manim_main``).
    """
    names: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        keyword, _, token = line.strip().partition(" ")
        target = _unquote(token.strip()).removeprefix("file:")
        if keyword == "file" and target:
            names.append(PurePosixPath(target.replace("\\", "/")).name)
    return names


def _unquote(token: str) -> str:
    """One token of a list FFmpeg reads: text in single quotes is taken as it is, and elsewhere ``\\`` escapes."""
    characters: list[str] = []
    quoted = False
    rest = iter(token)
    for character in rest:
        if character == "'":
            quoted = not quoted
        elif character == "\\" and not quoted:
            characters.append(next(rest, ""))
        else:
            characters.append(character)
    return "".join(characters)
