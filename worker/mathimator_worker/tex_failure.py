"""Turn a Manim stderr tail into the section 6 LaTeX failure message."""

from __future__ import annotations


def format_render_failure(return_code: int, tail: str) -> str:
    """Return ``LaTeX failed.`` plus one TeX log line, or Manim's status."""
    line = tex_log_line(tail)
    if line is not None:
        return f"LaTeX failed.\n{line}"
    body = tail.strip()
    if body:
        return f"manim exited with status {return_code}\n{body}"
    return f"manim exited with status {return_code}"


def tex_log_line(tail: str) -> str | None:
    """The TeX log line to show, or None when this tail is not a LaTeX failure."""
    if not _looks_like_latex(tail):
        return None
    for raw in tail.splitlines():
        line = raw.strip()
        if line.startswith("!") or "LaTeX Error" in line or "Emergency stop" in line:
            return line
    for raw in reversed(tail.splitlines()):
        line = raw.strip()
        if line and "latex" in line.lower():
            return line
    return "the TeX log did not include an error line"


def _looks_like_latex(tail: str) -> bool:
    lowered = tail.lower()
    if "latex" not in lowered and ".log" not in lowered:
        return False
    markers = ("error", "failed", "emergency stop", "\n!")
    return any(marker in lowered for marker in markers) or "! " in tail
