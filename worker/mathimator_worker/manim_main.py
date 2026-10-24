"""Manim's command line with FFmpeg's quoting fixed (D61, G4.3).

``python -m mathimator_worker.manim_main render ...`` runs what
``python -m manim render ...`` runs, with one change. Manim 0.22 writes the
segment list that FFmpeg joins with each path in single quotes, and does not
escape a quote inside one, so an apostrophe in the project path made FFmpeg
open a file that does not exist and failed every video. The list now writes an
apostrophe the way FFmpeg reads one. This module runs in the Manim process:
the supervisor never imports Manim.
"""

from __future__ import annotations

import importlib
import runpy
from pathlib import Path

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


def main() -> None:
    # Imported by name: the worker is type-checked without the render stack.
    writer = importlib.import_module("manim.scene.scene_file_writer").SceneFileWriter
    writer._concat_manifest_bytes = staticmethod(concat_manifest_bytes)
    runpy.run_module("manim", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
