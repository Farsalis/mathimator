"""The project path Manim and TeX get, checked before a render starts (D61).

Manim runs from the project's ``.mathimator/codegen`` with relative paths, so
TeX never reads the user's folder names and ``%``, ``~``, ``^^`` and ``'``
need no check (G4.5); ``manim_main`` quotes the apostrophe for FFmpeg (G4.3).
Three things are left for this preflight:

- Manim formats its absolute scene path as a template while it holds a ``{``,
  so such a path cannot render at all (G4.4).
- dvisvgm reads its arguments, and the folder it runs in, in the ANSI code
  page. A path with other characters goes to Manim through its 8.3 alias
  when Windows keeps one, and refuses formulas otherwise (G4.6).
- dvisvgm cannot open a path of MAX_PATH characters or more, even with
  Windows long paths on, and without them neither can Python, which watches
  the partial movies (G4.7).

A refusal is ``path_unsupported``. The alias is used only for Manim; every
path the worker sends stays in the long form (contract C5).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from mathimator_worker.document_error import DocumentError

if sys.platform == "win32":
    import winreg

    def _long_paths_enabled() -> bool:
        """Whether Windows lets a program that asks for it use paths of MAX_PATH characters or more."""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
                value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
        except OSError:
            return False
        return bool(value == 1)


MAX_PATH = 260
# The longest files a render writes below the project: a TeX output, named by
# its 16-digit hash, and a partial movie of the largest quality and longest
# suffix, named by its 64-digit hash.
_MEDIA = Path(".mathimator", "cache", "media")
TEX_FILE = _MEDIA / "Tex" / f"{'0' * 16}.dvi"
PARTIAL_FILE = _MEDIA / "videos" / "scene" / "1080p60" / "partial_movie_files" / "MainScene" / f"{'0' * 64}.webm"


def manim_root(project: Path, *, latex: bool) -> Path:
    """The project folder as Manim gets it: ``project``, or its 8.3 alias where TeX needs one.

    ``latex`` says whether the scene runs LaTeX. Raises DocumentError
    ``path_unsupported`` when the path cannot render.
    """
    root = project
    if not _in_ansi_code_page(str(project)):
        alias = short_path(project)
        if _in_ansi_code_page(str(alias)):
            root = alias
        elif latex:
            raise _unsupported(project, "its path has characters that Windows cannot pass to LaTeX")
    if "{" in str(root):
        raise _unsupported(project, 'its path contains "{"')
    if sys.platform == "win32":
        if latex and len(str(root)) > longest_folder(TEX_FILE):
            raise _too_long(project, "formulas and axis labels need", TEX_FILE)
        if len(str(project)) > longest_folder(PARTIAL_FILE) and not _long_paths_enabled():
            raise _too_long(project, "without Windows long paths, renders need", PARTIAL_FILE)
    return root


def longest_folder(tail: Path) -> int:
    """The longest folder path whose ``tail`` stays under MAX_PATH."""
    return MAX_PATH - 2 - len(str(tail))


def short_path(path: Path) -> Path:
    """``path`` with each name that has an 8.3 alias spelled by it, or ``path`` itself when Windows gives none."""
    if sys.platform != "win32":
        return path
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetShortPathNameW.restype = wintypes.DWORD
    # Room for the longest path Windows allows.
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetShortPathNameW(str(path), buffer, len(buffer))
    return Path(buffer.value) if 0 < length < len(buffer) else path


def _too_long(project: Path, need: str, tail: Path) -> DocumentError:
    length = len(str(project))
    return _unsupported(project, f"{need} a folder path of at most {longest_folder(tail)} characters, not {length}")


def _unsupported(project: Path, reason: str) -> DocumentError:
    message = f"Mathimator cannot render the project in {project}: {reason}. Move it to a shorter or plainer path."
    return DocumentError(message, code="path_unsupported")


def _in_ansi_code_page(text: str) -> bool:
    """Whether Windows passes ``text`` to a program that reads the ANSI code page with no character replaced."""
    if sys.platform != "win32":
        return True
    try:
        # Strict mbcs refuses the best-fit stand-ins, and takes everything when the code page is UTF-8.
        text.encode("mbcs")
    except UnicodeEncodeError:
        return False
    return True
