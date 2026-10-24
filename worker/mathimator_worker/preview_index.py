"""The preview index: each beat's derived time and its preview files.

The supervisor keeps it, writes it to ``preview-index.json`` and sends it in
every ``state`` reply. The UI chooses the file under the playhead from it; the
worker has no scrub command (D44).
"""

from __future__ import annotations

from dataclasses import dataclass


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
