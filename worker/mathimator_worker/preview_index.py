"""The preview index: each beat's derived time and its preview files.

The supervisor keeps it, writes it to ``preview-index.json`` and sends it in
every ``state`` reply. The UI chooses the file under the playhead from it; the
worker has no scrub command (D44).

In memory and in ``state`` the paths are absolute. On disk they are relative
to the project folder, so a copied or moved project still finds its own
media (F092). A stored path that does not lie inside the project's media or
last-good folder is dropped when the index is read (D18, F035).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass
class IndexBeat:
    id: str
    index: int
    derived_start: float
    duration: float
    partial: str | None
    last_good: str | None
    fresh: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "index": self.index,
            "derivedStart": self.derived_start,
            "duration": self.duration,
            "partial": self.partial,
            "lastGood": self.last_good,
            "fresh": self.fresh,
        }


@dataclass
class PreviewIndex:
    output: str | None
    beats: list[IndexBeat]

    def to_dict(self) -> dict[str, object]:
        return {
            "output": self.output,
            "beats": [beat.to_dict() for beat in self.beats],
        }

    def stored(self, root: Path) -> dict[str, object]:
        """The ``preview-index.json`` form: every path relative to ``root``, with forward slashes."""
        return self.with_paths(lambda path: _relative(path, root)).to_dict()

    def with_paths(self, convert: Callable[[str], str | None]) -> PreviewIndex:
        """A copy with every path passed through ``convert``. A path it maps to None is dropped."""

        def mapped(path: str | None) -> str | None:
            return None if path is None else convert(path)

        beats = [replace(beat, partial=mapped(beat.partial), last_good=mapped(beat.last_good)) for beat in self.beats]
        return PreviewIndex(output=mapped(self.output), beats=beats)


def load_index(raw: object, root: Path, folders: Sequence[Path]) -> PreviewIndex | None:
    """Read a stored index, or None when ``raw`` is not one.

    Paths are taken relative to ``root``. One that does not lie inside one of
    ``folders`` is dropped. The check is lexical, so a path from elsewhere
    never reaches the file system.
    """
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
                partial=_as_string(item.get("partial")),
                last_good=_as_string(item.get("lastGood")),
                fresh=item.get("fresh") is True,
            )
        )
    stored = PreviewIndex(output=_as_string(raw.get("output")), beats=beats)
    return stored.with_paths(lambda path: _contained(root, path, folders))


def _relative(path: str, root: Path) -> str | None:
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return None


def _contained(root: Path, stored: str, folders: Sequence[Path]) -> str | None:
    """``stored`` as an absolute path below one of ``folders``, spelled from ``root``, or None."""
    path = Path(os.path.normpath(os.path.join(root, stored)))
    for folder in folders:
        if path != folder and path.is_relative_to(folder):
            return str(folder / path.relative_to(folder))
    return None


def _as_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
