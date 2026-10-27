"""The ``render_failed`` message for a Manim run that exited non-zero.

A failure is a LaTeX failure only when Manim's own TeX markers are in its
output: the "LaTeX compilation error:" it logs, or the exception it raises when
a TeX compiler fails. A project folder, a user name or a formula that merely
mentions LaTeX or ``.log`` does not make one. A LaTeX failure reads
"LaTeX failed." followed by TeX's ``!`` error line, the ``<argument>`` line
that ends at the bad command when TeX shows one, and the ``l.N`` line that
names where TeX stopped, which for a formula is MathTex's ``\\end{align*}``.
They are taken from the TeX log on disk, since Rich wraps Manim's copy of them
to its own width. Any other failure is one line,
"manim exited with status N: <ExceptionType: text>", the exception Rich prints
below its traceback frames. The whole output stays in worker.log.
"""

from __future__ import annotations

import re
from pathlib import Path

# Tolerant of the line breaks Rich puts between words.
_TEX_MARKERS = re.compile(
    r"LaTeX\s+compilation\s+error:"
    r"|\b(?:latex|pdflatex|xelatex|lualatex)\s+error\s+converting\s+to\s+(?:dvi|xdv|pdf)\b"
    r"|\bfailed\s+but\s+did\s+not\s+produce\s+a\s+log\s+file\b"
)
# The line a Python traceback ends with, such as "ValueError: math domain error".
_EXCEPTION = re.compile(r"(?:[A-Za-z_]\w*\.)*[A-Z]\w*(?:Error|Exception|Exit|Interrupt)\b(?::.*)?")
_TEX_LOCATION = re.compile(r"l\.\d+\b")
# Rich draws traceback frames with box-drawing characters.
_BOX_DRAWING = range(0x2500, 0x2580)


def format_render_failure(return_code: int, output: str, tex_log: str | None = None) -> str:
    """The message for a run that exited with ``return_code`` after writing ``output``.

    ``tex_log`` is the text of the TeX log the run wrote, if it wrote one.
    """
    exception = exception_line(output)
    if _TEX_MARKERS.search(output):
        lines = tex_error_lines(tex_log or "") or ([exception] if exception else [])
        return "\n".join(["LaTeX failed.", *lines])
    reason = exception or _last_line(output)
    status = f"manim exited with status {return_code}"
    return f"{status}: {reason}" if reason else status


def tex_error_lines(log: str) -> list[str]:
    """TeX's first ``!`` error line with its ``<argument>`` and ``l.N`` lines, or none when the log records no error.

    TeX shows an error's context after its ``!`` line, down to the ``l.N`` line.
    Of the lines before ``l.N`` only an ``<argument>`` line is kept, which ends
    at the command TeX stopped on.
    """
    lines = [line.rstrip() for line in log.splitlines()]
    for number, line in enumerate(lines):
        if line.startswith("!"):
            context = lines[number + 1 :]
            end = next((index for index, later in enumerate(context) if _TEX_LOCATION.match(later)), None)
            if end is None:
                return [line]
            argument = next(([later] for later in context[:end] if later.startswith("<argument>")), [])
            return [line, *argument, context[end]]
    return []


def failed_tex_log(logs: list[Path]) -> str | None:
    """The text of the newest of ``logs`` that records a TeX error, or None. ``logs`` are oldest first."""
    for path in reversed(logs):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if tex_error_lines(text):
            return text
    return None


def exception_line(output: str) -> str | None:
    """The exception that ended ``output``, with the lines Rich wrapped it onto joined, or None."""
    lines = [line.strip() for line in output.splitlines()]
    for number in range(len(lines) - 1, -1, -1):
        if _EXCEPTION.fullmatch(lines[number]):
            return " ".join(line for line in lines[number:] if line)
    return None


def _last_line(output: str) -> str | None:
    """The last line of ``output`` that is not part of a traceback frame."""
    for line in reversed(output.splitlines()):
        text = line.strip()
        if text and ord(text[0]) not in _BOX_DRAWING:
            return text
    return None
