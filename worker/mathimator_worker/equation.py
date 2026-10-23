"""Translate the supported LaTeX subset into a Typst math document.

The shell runs ``python -m mathimator_worker.equation`` and then Typst.
This module does not import the supervisor, and it does not render a scene.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

_PAGE = "#set page(width: auto, height: auto, margin: 8pt, fill: none)"

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

_PASSTHROUGH = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789^_+-=()[]")
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

    def parse(self) -> str | None:
        parts: list[str] = []
        while self.index < self.length:
            piece = self._piece()
            if piece is None:
                return None
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
        if self.index >= self.length or self.source[self.index] != "{":
            return None
        self.index += 1
        parts: list[str] = []
        while self.index < self.length and self.source[self.index] != "}":
            piece = self._piece()
            if piece is None:
                return None
            parts.append(piece)
        if self.index >= self.length or self.source[self.index] != "}":
            return None
        self.index += 1
        return "".join(parts)


def _is_command_letter(char: str) -> bool:
    return char.isascii() and char.isalpha()


def main() -> int:
    """Read one JSON object ``{"latex": "..."}`` and write the translation."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write("equation translator expected a JSON object\n")
        return 2
    if not isinstance(payload, dict):
        sys.stderr.write("equation translator expected a JSON object\n")
        return 2
    latex = payload.get("latex")
    if not isinstance(latex, str):
        sys.stderr.write("equation translator expected a latex string\n")
        return 2
    result = translate(latex)
    if not result.supported or result.document is None:
        sys.stdout.write(json.dumps({"supported": False}) + "\n")
        return 0
    sys.stdout.write(json.dumps({"supported": True, "document": result.document}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
