"""Every edit op of contract C4.1, and the template check of an edit (F072, F094).

Load rewrites every field a template owns from its template. An edit that
changes such a field has to change the template too, or reading the edited
document back would quietly undo it. ``Session.edited`` reads every edit
back and refuses one that changed on the way, naming the field.
"""

from __future__ import annotations

import pytest

from mathimator_worker import session as session_module
from mathimator_worker.codegen import _ANCHOR_CALL
from mathimator_worker.document import ANCHORS, Project, dumps_project, empty_project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.session import Session
from tests.support import REPO

REFERENCE_60 = REPO / "tests" / "fixtures" / "reference-60" / "project.json"
_SETUP: dict[str, list[dict[str, object]]] = {
    "reference": [],
    "text": [{"op": "add_text", "afterBeatId": None}],
    "transform": [{"op": "add_formula_transform", "afterBeatId": None}],
    "empty": [],
    "surface": [{"op": "add_three_d_axes", "afterBeatId": None}],
    "curve": [{"op": "add_three_d_axes", "afterBeatId": None}, {"op": "set_three_d_mode", "mode": "curve"}],
}

# Each op with a document it applies to. The 2D template ops edit the plot, the
# axes and the formulas the templates own; the 3D ones the surface and the curve.
CASES: list[tuple[str, dict[str, object]]] = [
    ("reference", {"op": "set_project_name", "name": "Lesson"}),
    ("reference", {"op": "set_object_name", "objectId": "plot_1", "name": "Wave"}),
    ("reference", {"op": "set_object_color", "objectId": "plot_1", "color": "#ff0000"}),
    ("reference", {"op": "set_expression", "objectId": "plot_1", "expression": "cos(x)"}),
    ("reference", {"op": "set_latex", "objectId": "title_1", "latex": "y = x^2"}),
    ("text", {"op": "set_text", "objectId": "text_1", "text": "Hello"}),
    ("reference", {"op": "set_anchor", "objectId": "title_1", "anchor": "origin"}),
    ("reference", {"op": "set_object_shift", "objectId": "title_1", "shift": [1, 0.5]}),
    ("reference", {"op": "set_axes_range", "objectId": "axes_1", "axis": "x", "value": [-5, 5, 1]}),
    ("reference", {"op": "set_length", "objectId": "axes_1", "axis": "y", "value": 3}),
    ("reference", {"op": "set_show_labels", "objectId": "axes_1", "value": False}),
    ("reference", {"op": "set_domain", "objectId": "plot_1", "xMin": -3, "xMax": 3}),
    ("reference", {"op": "set_beat_duration", "beatId": "beat_1", "duration": 3}),
    ("reference", {"op": "set_beat_color", "beatId": "beat_5", "color": "#00FF00"}),
    ("reference", {"op": "set_beat_shift", "beatId": "beat_6", "shift": [0, 1]}),
    ("reference", {"op": "add_axes_function", "afterBeatId": "beat_2"}),
    ("reference", {"op": "add_formula_transform", "afterBeatId": None}),
    ("empty", {"op": "add_three_d_axes", "afterBeatId": None}),
    ("reference", {"op": "add_formula", "afterBeatId": "beat_1", "latex": "a^2"}),
    ("reference", {"op": "add_text", "afterBeatId": None}),
    ("reference", {"op": "delete_object", "objectId": "plot_1"}),
    ("surface", {"op": "set_three_d_mode", "mode": "curve"}),
    ("surface", {"op": "set_camera", "key": "phi", "value": 70}),
    ("surface", {"op": "set_resolution", "objectId": "surface_1", "value": [12, 12]}),
    ("curve", {"op": "set_curve_component", "objectId": "curve_1", "index": 2, "expression": "t / 2"}),
    (
        "reference",
        {"op": "set_export_profile", "format": "webm", "width": 1280, "height": 720, "fps": 30, "transparent": True},
    ),
    ("transform", {"op": "set_object_color", "objectId": "formula_1", "color": "#E07A5F"}),
    ("transform", {"op": "set_latex", "objectId": "formula_2", "latex": "y = \\tan(x)"}),
    ("transform", {"op": "set_anchor", "objectId": "formula_2", "anchor": "edge_down"}),
    ("transform", {"op": "set_object_shift", "objectId": "formula_1", "shift": [0, -1]}),
    ("transform", {"op": "delete_object", "objectId": "formula_2"}),
    ("surface", {"op": "set_expression", "objectId": "surface_1", "expression": "x*y"}),
    ("surface", {"op": "set_axes_range", "objectId": "axes_1", "axis": "z", "value": [-3, 3, 1]}),
    ("surface", {"op": "set_object_color", "objectId": "surface_1", "color": "#81B29A"}),
    ("curve", {"op": "set_object_color", "objectId": "curve_1", "color": "#81B29A"}),
    ("curve", {"op": "delete_object", "objectId": "axes_1"}),
]


def test_every_op_has_a_case() -> None:
    assert {edit["op"] for _start, edit in CASES} == set(session_module._EDITS)


@pytest.mark.parametrize(("start", "edit"), CASES, ids=[f"{start}-{edit['op']}" for start, edit in CASES])
def test_an_edit_survives_reading_the_document_back(start: str, edit: dict[str, object]) -> None:
    session = Session(_document(start))
    before = dumps_project(session.project)
    session.edit(edit)
    saved = dumps_project(session.project)
    assert saved != before
    assert dumps_project(load_text(saved)) == saved


def test_an_edit_that_skips_its_template_is_refused_and_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(_document("reference"))
    before = dumps_project(session.project)
    # The plot's expression belongs to the axes_function template, which this edit now leaves alone.
    monkeypatch.setattr(session_module, "_template_plot", lambda *args: None)
    with pytest.raises(DocumentError) as refused:
        session.edit({"op": "set_expression", "objectId": "plot_1", "expression": "cos(x)"})
    field = "scene.objects[1].params.expression"
    assert (refused.value.code, refused.value.field) == ("invalid_edit", None)
    assert str(refused.value) == f"{field} is set by its template, so this edit cannot change it."
    assert dumps_project(session.project) == before
    assert not session.can_undo()


def test_codegen_places_every_anchor() -> None:
    assert set(_ANCHOR_CALL) == ANCHORS


def _document(start: str) -> Project:
    """The reference-60 lesson, or an empty project, with the setup edits of ``start``."""
    lesson = start in {"reference", "text", "transform"}
    session = Session(load_text(REFERENCE_60.read_text(encoding="utf-8")) if lesson else empty_project())
    for edit in _SETUP[start]:
        session.edit(edit)
    return session.project
