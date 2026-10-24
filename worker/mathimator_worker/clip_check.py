"""Tell a finished partial movie from one that a killed Manim cut off.

Manim reuses any segment file that exists, so a cut-off clip would break every
later render of its beat. The checks read container structure with the
standard library only:

- MP4 and MOV: the top-level boxes add up to exactly the file length, none
  has size 0, and one of them is ``moov``. FFmpeg writes ``moov`` when it
  closes the file, so a cut-off file has none or only part of one. It writes
  size 0 as a placeholder for ``mdat`` and ``moov`` and fills in the real size
  before it closes a seekable file, which every segment is.
- WebM: an EBML header, then top-level elements of known size that add up to
  exactly the file length, one of them a Segment. FFmpeg writes the Segment
  with an unknown size and fills the size in when it closes the file.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import BinaryIO

SEGMENT_SUFFIXES = frozenset({".mp4", ".mov", ".webm"})

_EBML_HEADER = 0x1A45DFA3
_SEGMENT = 0x18538067


def clip_is_complete(path: Path) -> bool:
    """Return True when ``path`` is a structurally complete MP4, MOV or WebM file."""
    suffix = path.suffix.lower()
    if suffix not in SEGMENT_SUFFIXES:
        return False
    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if suffix == ".webm":
                return _matroska_complete(handle, size)
            return _boxes_complete(handle, size)
    except OSError:
        return False


class ClipChecker:
    """``clip_is_complete`` that remembers the clips that passed by size and mtime."""

    def __init__(self) -> None:
        self._passed: dict[Path, tuple[int, int]] = {}

    def complete(self, path: Path) -> bool:
        try:
            info = path.stat()
        except OSError:
            return False
        stamp = (info.st_size, info.st_mtime_ns)
        if self._passed.get(path) == stamp:
            return True
        if not clip_is_complete(path):
            return False
        self._passed[path] = stamp
        return True


def _boxes_complete(handle: BinaryIO, size: int) -> bool:
    offset = 0
    has_moov = False
    while offset < size:
        handle.seek(offset)
        header = handle.read(16)
        if len(header) < 8:
            return False
        length, kind = struct.unpack_from(">I4s", header)
        header_size = 8
        if length == 1:
            if len(header) < 16:
                return False
            (length,) = struct.unpack_from(">Q", header, 8)
            header_size = 16
        if length < header_size:
            return False
        has_moov = has_moov or kind == b"moov"
        offset += length
    return offset == size and has_moov


def _matroska_complete(handle: BinaryIO, size: int) -> bool:
    offset = 0
    has_segment = False
    while offset < size:
        handle.seek(offset)
        head = handle.read(12)
        element = _read_vint(head, 0, max_length=4, keep_marker=True)
        if element is None:
            return False
        element_id, id_length = element
        if offset == 0 and element_id != _EBML_HEADER:
            return False
        declared = _read_vint(head, id_length, max_length=8, keep_marker=False)
        if declared is None:
            return False
        length, size_length = declared
        if length == (1 << (7 * size_length)) - 1:
            # All value bits set is the "unknown size" the muxer never filled in.
            return False
        has_segment = has_segment or element_id == _SEGMENT
        offset += id_length + size_length + length
    return offset == size and has_segment


def _read_vint(data: bytes, start: int, *, max_length: int, keep_marker: bool) -> tuple[int, int] | None:
    """Decode one EBML variable-length integer. Return (value, byte length)."""
    if start >= len(data) or data[start] == 0:
        return None
    first = data[start]
    length = 9 - first.bit_length()
    if length > max_length or start + length > len(data):
        return None
    value = first if keep_marker else first & (0xFF >> length)
    for byte in data[start + 1 : start + length]:
        value = (value << 8) | byte
    return value, length
