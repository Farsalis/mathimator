"""Manim's command line with two faults of its scene writer fixed (spec section 5).

``python -m mathimator_worker.manim_main render ...`` runs what
``python -m manim render ...`` runs, with two changes. Manim 0.22 writes the
segment list that FFmpeg joins with each path in single quotes, and does not
escape a quote inside one, so an apostrophe in the project path made FFmpeg
open a file that does not exist and failed every video. The list now writes an
apostrophe the way FFmpeg reads one. And a PNG sequence now holds one image
per frame, the held frames of a wait included. This module runs in the Manim
process: the supervisor never imports Manim.
"""

from __future__ import annotations

import importlib
import runpy
from pathlib import Path
from typing import Any

# Manim's first line of the segment list.
_HEADER = "# This file records the segment order used by Manim.\n"


def concat_manifest_bytes(input_files: list[str]) -> bytes:
    """Manim's segment list for ``input_files``, each path quoted the way FFmpeg's concat demuxer reads it.

    FFmpeg takes everything inside single quotes as it is, so a quote in a
    path closes the quoted text, follows as ``\\'``, and opens a new one:
    ``'\\''``.
    """
    entries = (f"file:{Path(name).as_posix()}".replace("'", "'\\''") for name in input_files)
    return (_HEADER + "".join(f"file '{entry}'\n" for entry in entries)).encode("utf-8")


def write_every_frame(writer: Any) -> None:
    """Make the scene writer class ``writer`` save one image per frame of a PNG sequence.

    Manim 0.22 draws a static wait once and passes that frame to ``write_frame``
    with the number of frames it holds as ``repeat``. A movie encodes it that
    many times, but an image sequence saved it once, so a 1 s wait at 30 fps
    was 1 PNG instead of 30. Movies are written as before.
    """
    write_frame = writer.write_frame

    def write_each_frame(self: Any, pixels: object, *, repeat: int = 1) -> None:
        if self.output_spec.is_image_sequence:
            for _ in range(repeat):
                write_frame(self, pixels)
        else:
            write_frame(self, pixels, repeat=repeat)

    writer.write_frame = write_each_frame


def main() -> None:
    # Imported by name: the worker is type-checked without the render stack.
    writer = importlib.import_module("manim.scene.scene_file_writer").SceneFileWriter
    writer._concat_manifest_bytes = staticmethod(concat_manifest_bytes)
    write_every_frame(writer)
    runpy.run_module("manim", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
