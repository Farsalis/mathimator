"""Turn Manim's log into ordered partial-movie events.

Manim 0.22 logs each finished segment as
``Animation N : Partial movie file written in '<path>'`` and each cache hit as
``Animation N : Using cached data (hash : <digest>)``. With one encoder in
flight those lines are the beat order. The supervisor also reads
``partial_movie_file_list.txt`` when the render ends, which is the segment
order Manim concatenated. A partial counts only when its container is
complete (``clip_check``). The first one that is not stops the beat order and
is kept in ``incomplete``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mathimator_worker.clip_check import clip_is_complete

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_WRITTEN = re.compile(
    r"Animation\s+(\d+)\s*:\s*Partial movie file written in\s+'([^']+?\.mp4)'",
    re.IGNORECASE,
)
_CACHED = re.compile(
    r"Animation\s+(\d+)\s*:\s*Using cached data\s*\(\s*hash\s*:\s*([0-9a-fA-F\s]+)\s*\)",
    re.IGNORECASE,
)
_READY = re.compile(r"File ready at\s+'([^']+)'", re.IGNORECASE)
_IMAGES = re.compile(r"\d+\s+images ready at\s+(\S.*?)\s*$", re.IGNORECASE)
# Rich wraps the folder over several lines, but the count stays beside "images".
_FRAMES = re.compile(r"(\d+)\s+images ready at", re.IGNORECASE)


@dataclass
class ManimProgress:
    media_dir: Path
    beats: dict[int, Path] = field(default_factory=dict)
    output_path: Path | None = None
    frame_count: int | None = None
    incomplete: list[Path] = field(default_factory=list)
    _buffer: str = field(default="", init=False, repr=False)

    def feed(self, text: str) -> list[tuple[int, Path]]:
        self._buffer += text
        self._buffer = _ANSI.sub("", self._buffer)
        found = self._beats_in_buffer()
        for match in _READY.finditer(self._buffer):
            candidate = clean_logged_path(match.group(1))
            if "partial_movie_files" in candidate.parts:
                continue
            self.output_path = candidate
        for match in _IMAGES.finditer(self._buffer):
            self.output_path = clean_logged_path(match.group(1))
        for match in _FRAMES.finditer(self._buffer):
            self.frame_count = int(match.group(1))
        return self._take_in_order(found)

    def flush_manifest(self) -> list[tuple[int, Path]]:
        manifest = newest_manifest(self.media_dir)
        if manifest is None:
            return []
        ordered = read_manifest(manifest)
        found = {index: path for index, path in enumerate(ordered)}
        return self._take_in_order(found)

    def _beats_in_buffer(self) -> dict[int, Path]:
        found: dict[int, Path] = {}
        for match in _WRITTEN.finditer(self._buffer):
            found[int(match.group(1))] = clean_logged_path(match.group(2))
        for match in _CACHED.finditer(self._buffer):
            index = int(match.group(1))
            if index in found:
                continue
            digest = re.sub(r"\s+", "", match.group(2))
            path = find_partial(self.media_dir, digest)
            if path is not None:
                found[index] = path
        return found

    def _take_in_order(self, found: dict[int, Path]) -> list[tuple[int, Path]]:
        events: list[tuple[int, Path]] = []
        while True:
            index = len(self.beats)
            path = found.get(index)
            if path is None or not path.is_file():
                break
            if "partial_movie_files" not in path.parts:
                break
            if not self._complete(path):
                break
            self.beats[index] = path
            events.append((index, path))
        return events

    def _complete(self, path: Path) -> bool:
        if path in self.incomplete:
            return False
        if clip_is_complete(path):
            return True
        self.incomplete.append(path)
        return False


def clean_logged_path(raw: str) -> Path:
    collapsed = raw.replace("\n", "").replace("\r", "").strip()
    direct = Path(collapsed)
    if direct.is_file():
        return direct
    squashed = Path(re.sub(r"\s+", "", collapsed))
    if squashed.is_file():
        return squashed
    return direct


def find_partial(media_dir: Path, digest: str) -> Path | None:
    if not digest:
        return None
    name = f"{digest}.mp4"
    matches = [path for path in media_dir.rglob(name) if "partial_movie_files" in path.parts and path.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def newest_manifest(media_dir: Path) -> Path | None:
    manifests = [path for path in media_dir.rglob("partial_movie_file_list.txt") if path.is_file()]
    if not manifests:
        return None
    manifests.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return manifests[0]


_VIDEO_SUFFIXES = {".mp4", ".gif", ".webm", ".mov"}


def discard_unfinished_partials(
    media_dir: Path,
    started: float,
    finished: set[Path],
) -> list[Path]:
    """Delete media files a killed Manim left half-written.

    Manim treats any existing partial-movie file as a cache hit. Cancelling
    during an animation leaves a short file, and the next render concatenates
    it. GIF and MP4 assembly then fail while decoding that file.
    """
    if not media_dir.is_dir():
        return []
    keep = {path.resolve() for path in finished}
    removed: list[Path] = []
    for path in media_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        is_partial = "partial_movie_files" in path.parts and suffix in _VIDEO_SUFFIXES
        is_scene_movie = (
            path.name.startswith("MainScene.") and suffix in _VIDEO_SUFFIXES and "partial_movie_files" not in path.parts
        )
        is_manifest = path.name == "partial_movie_file_list.txt"
        if not is_partial and not is_scene_movie and not is_manifest:
            continue
        try:
            modified = path.stat().st_mtime
            resolved = path.resolve()
        except OSError:
            continue
        if modified < started - 1:
            continue
        if resolved in keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed


def read_manifest(path: Path) -> list[Path]:
    ordered: list[Path] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("file "):
            continue
        quoted = stripped.split("'", 2)
        if len(quoted) < 2:
            continue
        raw = quoted[1]
        if raw.startswith("file:"):
            raw = raw[5:]
        ordered.append(Path(raw))
    return ordered
