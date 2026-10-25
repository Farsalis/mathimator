"""The preview index: each beat's derived time and its preview files.

The supervisor keeps it, writes it to ``preview-index.json`` and sends it in
every ``state`` reply. The UI chooses the file under the playhead from it; the
worker has no scrub command (D44).

In memory and in ``state`` the paths are absolute. On disk they are relative
to the project folder, so a copied or moved project still finds its own
media (F092). A stored path that does not lie inside the project's media or
last-good folder is dropped when the index is read (D18, F035).

Each file carries the chain key of the beat it was rendered for
(``identity.beat_chain_keys``), so a file counts as fresh only for the
document it shows (F135). The keys stay on disk and are not sent in ``state``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from mathimator_worker.document import Beat, derived_starts


@dataclass
class IndexBeat:
    id: str
    index: int
    derived_start: float
    duration: float
    partial: str | None
    last_good: str | None
    fresh: bool
    # The chain keys the partial and the last good file were rendered for.
    partial_key: str | None
    last_good_key: str | None

    def to_dict(self) -> dict[str, object]:
        """The beat as ``state`` sends it."""
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
        """The index as ``state`` sends it."""
        return {
            "output": self.output,
            "beats": [beat.to_dict() for beat in self.beats],
        }

    def stored(self, root: Path) -> dict[str, object]:
        """The ``preview-index.json`` form: every path relative to ``root``, with forward slashes, and the keys."""
        relative = self.with_paths(lambda path: _relative(path, root))
        beats = [
            {**beat.to_dict(), "partialKey": beat.partial_key, "lastGoodKey": beat.last_good_key}
            for beat in relative.beats
        ]
        return {"output": relative.output, "beats": beats}

    def with_paths(self, convert: Callable[[str], str | None]) -> PreviewIndex:
        """A copy with every path passed through ``convert``. A path it maps to None is dropped."""

        def mapped(path: str | None) -> str | None:
            return None if path is None else convert(path)

        beats = [replace(beat, partial=mapped(beat.partial), last_good=mapped(beat.last_good)) for beat in self.beats]
        return PreviewIndex(output=mapped(self.output), beats=beats)

    def record(self, partials: Iterable[tuple[int, Path]], keys: Sequence[str]) -> None:
        """Make each partial the fresh file of its beat, tagged with the chain key it was rendered for (F034, F135).

        ``partials`` pairs beat indexes with files, and ``keys`` are the chain
        keys of the document they were rendered from.
        """
        for index, path in partials:
            if index >= len(self.beats):
                continue
            beat = self.beats[index]
            beat.partial, beat.partial_key, beat.fresh = str(path), keys[index], True
            # A fresh partial replaces the older last good file (F034).
            beat.last_good = beat.last_good_key = None


def rebuild_index(
    previous: PreviewIndex | None,
    beats: Sequence[Beat],
    keys: Sequence[str],
    complete: Callable[[Path], bool],
    output_current: Callable[[], bool],
) -> PreviewIndex:
    """The index of a document with ``beats``, whose chain keys are ``keys``, kept from ``previous`` (F010, F034, F135).

    A beat is fresh when one of its files was rendered for it as it is now,
    that is, for its chain key. A stale beat keeps its newest file as last
    good, which the canvas shows under the badge until a new partial is
    ready. Only a file ``complete`` accepts counts (C14 Q3). The full movie
    stays only while every beat is fresh and ``output_current`` says it is the
    render of this very document.
    """
    prior_beats = {beat.id: beat for beat in previous.beats} if previous is not None else {}
    starts = derived_starts([beat.duration for beat in beats])
    fitted: list[IndexBeat] = []
    for index, beat in enumerate(beats):
        prior = prior_beats.get(beat.id)
        # The beat's files, newest first.
        files: list[tuple[str | None, str | None]] = []
        if prior is not None:
            files = [(prior.partial, prior.partial_key), (prior.last_good, prior.last_good_key)]
        usable = [(path, key) for path, key in files if path is not None and complete(Path(path))]
        current = next(((path, key) for path, key in usable if key == keys[index]), None)
        partial, partial_key = current if current is not None else (None, None)
        last_good, last_good_key = usable[0] if current is None and usable else (None, None)
        fitted.append(
            IndexBeat(
                id=beat.id,
                index=index,
                derived_start=float(starts[index]),
                duration=float(beat.duration),
                partial=partial,
                last_good=last_good,
                fresh=current is not None,
                partial_key=partial_key,
                last_good_key=last_good_key,
            )
        )
    output = previous.output if previous is not None else None
    if output is not None and not (all(beat.fresh for beat in fitted) and output_current() and complete(Path(output))):
        output = None
    return PreviewIndex(output=output, beats=fitted)


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
                partial_key=_as_string(item.get("partialKey")),
                last_good_key=_as_string(item.get("lastGoodKey")),
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
