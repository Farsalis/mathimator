"""Choose the preview file under a playhead.

Scrub reads the preview index and, when it needs to, checks that a file
exists. It does not import Manim or PyAV and it does not start a render.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def scrub_at(index: PreviewIndex | None, time: float) -> dict[str, object]:
    """Return the file and time the canvas should show at ``time`` seconds."""
    if index is None or not index.beats:
        return {
            "mode": "empty",
            "path": None,
            "time": 0,
            "beatIndex": None,
            "badge": False,
            "fresh": None,
        }
    if time < 0:
        time = 0.0
    chosen = _beat_at(index.beats, time)
    offset = round(time - chosen.derived_start, 3)
    if offset < 0:
        offset = 0.0
    all_fresh = all(beat.fresh for beat in index.beats)
    if all_fresh and _exists(index.output):
        return {
            "mode": "movie",
            "path": index.output,
            "time": round(time, 3),
            "beatIndex": chosen.index,
            "badge": False,
            "fresh": True,
        }
    if chosen.fresh and _exists(chosen.partial):
        return {
            "mode": "partial",
            "path": chosen.partial,
            "time": offset,
            "beatIndex": chosen.index,
            "badge": not all_fresh,
            "fresh": True,
        }
    if _exists(chosen.last_good):
        return {
            "mode": "partial",
            "path": chosen.last_good,
            "time": offset,
            "beatIndex": chosen.index,
            "badge": True,
            "fresh": False,
        }
    return {
        "mode": "blank",
        "path": None,
        "time": offset,
        "beatIndex": chosen.index,
        "badge": True,
        "fresh": False,
    }


def _beat_at(beats: list[IndexBeat], time: float) -> IndexBeat:
    for beat in beats:
        end = beat.derived_start + float(beat.duration)
        if time < end:
            return beat
    return beats[-1]


def _exists(path: str | None) -> bool:
    if not path:
        return False
    return Path(path).is_file()
