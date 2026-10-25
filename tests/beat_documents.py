"""Small project.json documents for the beat, shape and timeline tests.

Each builder returns raw JSON as a file holds it, so a test can break one field
and read it back with ``refused``. ``document`` numbers the beats ``beat_1``,
``beat_2`` and so on, which makes a refusal's ``scene.beats[i]`` easy to read.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mathimator_worker.beats import SHAPES
from mathimator_worker.document import Project, dumps_project, empty_project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.session import Session

Raw = dict[str, Any]


def document(objects: list[Raw], beats: list[Raw]) -> Raw:
    """A 2D project holding ``objects`` and ``beats``, the beats numbered in order."""
    raw: Raw = json.loads(dumps_project(empty_project()))
    raw["scene"]["objects"] = objects
    raw["scene"]["beats"] = [{"id": f"beat_{index}", **item} for index, item in enumerate(beats, start=1)]
    return raw


def three_d(beats: list[Raw], mode: str = "surface") -> Raw:
    """The 3D template (``axes_1`` and ``surface_1`` or ``curve_1``, created by beat_1 and beat_2), then ``beats``."""
    session = Session(empty_project())
    session.edit({"op": "add_three_d_axes", "afterBeatId": None})
    if mode == "curve":
        session.edit({"op": "set_three_d_mode", "mode": "curve"})
    raw: Raw = json.loads(dumps_project(session.project))
    raw["scene"]["beats"] += [{"id": f"beat_{index}", **item} for index, item in enumerate(beats, start=3)]
    return raw


def beat(action: str, *targets: str, params: Raw | None = None, easing: str | None = None, duration: float = 1) -> Raw:
    item: Raw = {"duration": duration, "action": action, "targets": list(targets)}
    if params is not None:
        item["params"] = params
    if easing is not None:
        item["easing"] = easing
    return item


def text(object_id: str = "text_1") -> Raw:
    return {
        "id": object_id,
        "name": "Text",
        "type": "text",
        "params": {"text": "Hi", "color": "#FFFFFF", "anchor": "origin"},
    }


def formula(object_id: str = "eq_1", latex: str = "x") -> Raw:
    return {
        "id": object_id,
        "name": "Formula",
        "type": "math_formula",
        "params": {"latex": latex, "color": "#FFFFFF", "anchor": "origin"},
    }


def axes(object_id: str = "axes_1") -> Raw:
    return {
        "id": object_id,
        "name": "Axes",
        "type": "axes",
        "params": {
            "xRange": [-4, 4, 1],
            "yRange": [-2, 2, 1],
            "xLength": 8,
            "yLength": 4,
            "color": "#FFFFFF",
            "showLabels": False,
        },
    }


def plot(object_id: str = "plot_1", axes_id: str = "axes_1") -> Raw:
    return {
        "id": object_id,
        "name": "Sine",
        "type": "function_plot",
        "params": {"axesId": axes_id, "expression": "sin(x)", "xMin": -3, "xMax": 3, "color": "#58C4DD"},
    }


def shape(shape_type: str, object_id: str | None = None, **params: Any) -> Raw:
    """A shape with the defaults of its row; a point on axes is at (1, 1) on ``axes_1`` unless told otherwise."""
    row = SHAPES[shape_type]
    values: Raw = {param.name: param.default for param in row.params}
    if shape_type == "axes_point":
        values.update(axesId="axes_1", x=1, y=1)
    values.update(params)
    return {"id": object_id or f"{row.id_prefix}_1", "name": row.name, "type": shape_type, "params": values}


def load(raw: Raw | str) -> Project:
    return load_text(raw if isinstance(raw, str) else json.dumps(raw))


def refused(raw: Raw | str) -> DocumentError:
    with pytest.raises(DocumentError) as caught:
        load(raw)
    return caught.value
