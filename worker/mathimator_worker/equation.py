"""Translate the supported LaTeX subset into a Typst math document.

This module does not import the supervisor, and it does not render a scene.
``tests/fixtures/equation/translate-cases.json`` holds the expected output
for every case, so a port of this translator can be checked against it.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

_PAGE = "#set page(width: auto, height: auto, margin: 8pt, fill: none)"
# \frac and \sqrt groups nested deeper than this are unsupported, as in the
# shell's translator. The bound keeps the recursion inside Python's limit.
MAX_DEPTH = 100

_COMMANDS = {
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "log": "log",
    "exp": "exp",
    "pi": "pi",
    "theta": "theta",
    "alpha": "alpha",
    "beta": "beta",
    "gamma": "gamma",
}

_ALNUM = frozenset(string.ascii_letters + string.digits)
_PASSTHROUGH = _ALNUM | frozenset("^_+-=()[]")
_WHITESPACE = frozenset(" \t\r\n")


@dataclass(frozen=True)
class Translation:
    """A supported document, or a refusal with no Typst math."""

    supported: bool
    math: str | None = None
    document: str | None = None


def translate(latex: str) -> Translation:
    """Return the Typst document for ``latex``, or an unsupported result.

    ``\\int`` and any other command, and any character outside the supported
    subset, refuse the whole formula. There is no partial document.
    """
    math = _Parser(latex).parse()
    if math is None:
        return Translation(supported=False)
    return Translation(supported=True, math=math, document=typst_document(math))


def typst_document(math: str) -> str:
    """Wrap translated math in the phase 3 page setup."""
    return f"{_PAGE}\n$ {math} $\n"


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.length = len(source)
        self.index = 0
        self.depth = 0

    def parse(self) -> str | None:
        return self._sequence(None)

    def _sequence(self, closing: str | None) -> str | None:
        """Translate pieces up to ``closing``, or to the end when it is None."""
        parts: list[str] = []
        while self.index < self.length and self.source[self.index] != closing:
            piece = self._piece()
            if piece is None:
                return None
            # Typst reads a run such as mc or x2 as one name. A space keeps
            # each letter, digit and command its own atom, as in LaTeX:
            # mc^2 becomes m c^2.
            if parts and parts[-1][-1] in _ALNUM and piece[0] in _ALNUM:
                parts.append(" ")
            parts.append(piece)
        return "".join(parts)

    def _piece(self) -> str | None:
        char = self.source[self.index]
        if char in _WHITESPACE:
            start = self.index
            self.index += 1
            while self.index < self.length and self.source[self.index] in _WHITESPACE:
                self.index += 1
            return self.source[start : self.index]
        if char == "\\":
            return self._command()
        if char in _PASSTHROUGH:
            self.index += 1
            return char
        return None

    def _command(self) -> str | None:
        self.index += 1
        if self.index >= self.length or not _is_command_letter(self.source[self.index]):
            return None
        start = self.index
        while self.index < self.length and _is_command_letter(self.source[self.index]):
            self.index += 1
        name = self.source[start : self.index]
        mapped = _COMMANDS.get(name)
        if mapped is not None:
            return mapped
        if name == "frac":
            numerator = self._group()
            if numerator is None:
                return None
            denominator = self._group()
            if denominator is None:
                return None
            return f"frac({numerator}, {denominator})"
        if name == "sqrt":
            body = self._group()
            if body is None:
                return None
            return f"sqrt({body})"
        return None

    def _group(self) -> str | None:
        if self.index >= self.length or self.source[self.index] != "{" or self.depth == MAX_DEPTH:
            return None
        self.index += 1
        self.depth += 1
        body = self._sequence("}")
        self.depth -= 1
        if body is None or self.index >= self.length:
            return None
        self.index += 1
        return body


def _is_command_letter(char: str) -> bool:
    return char.isascii() and char.isalpha()
