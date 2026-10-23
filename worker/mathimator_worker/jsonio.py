"""Stable project.json bytes and the separate canonical hash form.

Saved documents use two-space indentation, stable key order, and a trailing
newline. Short arrays of scalars stay on one line so the Journey A document
round-trips. The hash form sorts keys, drops insignificant whitespace, keeps
array order, and writes every number with three decimal places.
"""

from __future__ import annotations

import json
import math
from typing import Mapping, Sequence

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def dumps_document(value: JsonValue) -> str:
    """Pretty-print a document value with a trailing newline."""
    return _pretty(value, 0) + "\n"


def canonical_dumps(value: object) -> str:
    """Serialize ``value`` in the section 4 canonical hash form."""
    return _canonical(value)


def _pretty(value: JsonValue, indent: int) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        pad = " " * indent
        inner = " " * (indent + 2)
        lines = ["{"]
        items = list(value.items())
        for index, (key, item) in enumerate(items):
            comma = "," if index < len(items) - 1 else ""
            rendered = _pretty(item, indent + 2)
            lines.append(f"{inner}{json.dumps(key, ensure_ascii=False)}: {rendered}{comma}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(value, list):
        if all(_is_scalar(item) for item in value):
            return "[" + ", ".join(_scalar(item) for item in value) + "]"
        pad = " " * indent
        inner = " " * (indent + 2)
        lines = ["["]
        for index, item in enumerate(value):
            comma = "," if index < len(value) - 1 else ""
            lines.append(f"{inner}{_pretty(item, indent + 2)}{comma}")
        lines.append(f"{pad}]")
        return "\n".join(lines)
    return _scalar(value)


def _is_scalar(value: JsonValue) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("project.json cannot store a non-finite number")
        return json.dumps(value)
    raise TypeError(f"cannot store {type(value).__name__} in project.json")


def _canonical(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:.3f}"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cannot hash a non-finite number")
        return f"{value:.3f}"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, Mapping):
        parts = [
            json.dumps(str(key), ensure_ascii=False) + ":" + _canonical(value[key])
            for key in sorted(value, key=lambda item: str(item))
        ]
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"cannot hash {type(value).__name__}")
