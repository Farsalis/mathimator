"""Every edit op of contract C4.1, from ``tests/fixtures/protocol/edit-ops.json``, and the template check of an edit (F072, F094).

The fixture lists each op with the type of each field and examples, each
accepted on a start document; the UI's edit builders are tested against the
same file. Every example is replayed through a session: it changes the saved
document, which reads back unchanged, its focus names what is there, and undo
takes it back in one step. The examples of the beat and shape ops are checked
for what they leave in the document too; their refusals are in
``test_beat_edits.py``. ``test_edit_requests.py`` sends every example to a
supervisor as an ``edit`` request, as the shell does.

Load rewrites every field a template owns from its template. An edit that
changes such a field has to change the template too, or reading the edited
document back would quietly undo it. ``Session.edited`` reads every edit back
and refuses one that changed on the way, naming the field.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from mathimator_worker import session as session_module
from mathimator_worker.beats import EASINGS, KINDS, SHAPES
from mathimator_worker.codegen import _ANCHOR_CALL
from mathimator_worker.document import ANCHORS, FORMATS, Project, dumps_project, empty_project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.session import Session
from tests.support import REPO

FIXTURE: dict[str, Any] = json.loads(
    (REPO / "tests" / "fixtures" / "protocol" / "edit-ops.json").read_text(encoding="utf-8")
)
OPS: dict[str, Any] = FIXTURE["ops"]
EXAMPLES: list[tuple[str, dict[str, Any]]] = [
    (example["start"], example["edit"]) for op in OPS.values() for example in op["examples"]
]
EXAMPLE_IDS = [f"{start}-{edit['op']}" for start, edit in EXAMPLES]
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# What each example of a beat or shape op does: the beat and the object its reply focuses, and
# values of the saved document at JSON paths. Every example of these ops is listed, in fixture order.
Result = tuple[str, dict[str, Any], tuple[str | None, str | None], dict[str, Any]]
RESULTS: list[Result] = [
    (
        "reference",
        {"op": "add_beat", "afterBeatId": "beat_3", "action": "indicate", "targets": ["title_1"]},
        ("beat_9", "title_1"),
        {"scene.beats[3]": {"id": "beat_9", "duration": 1, "action": "indicate", "targets": ["title_1"]}},
    ),
    (
        "reference",
        {"op": "add_beat", "afterBeatId": None, "action": "wait", "targets": []},
        ("beat_9", None),
        {"scene.beats[8]": {"id": "beat_9", "duration": 1, "action": "wait", "targets": []}},
    ),
    (
        # title_1 left in beat_9 and enters again.
        "exited",
        {"op": "add_beat", "afterBeatId": None, "action": "write", "targets": ["title_1"]},
        ("beat_10", "title_1"),
        {"scene.beats[9]": {"id": "beat_10", "duration": 1, "action": "write", "targets": ["title_1"]}},
    ),
    (
        "shapes",
        {
            "op": "add_beat",
            "afterBeatId": None,
            "action": "scale",
            "targets": ["dot_1", "circle_1"],
            "params": {"factor": 2},
        },
        ("beat_15", "dot_1"),
        {
            "scene.beats[14]": {
                "id": "beat_15",
                "duration": 1,
                "action": "scale",
                "targets": ["dot_1", "circle_1"],
                "params": {"factor": 2},
            }
        },
    ),
    (
        "surface",
        {"op": "add_beat", "afterBeatId": None, "action": "move_camera", "targets": []},
        ("beat_3", None),
        {
            "scene.beats[2]": {
                "id": "beat_3",
                "duration": 1,
                "action": "move_camera",
                "targets": [],
                # A quarter turn from the template's camera (65, -45, 1).
                "params": {"phi": 65, "theta": 45, "zoom": 1},
            }
        },
    ),
    (
        "reference",
        {"op": "set_beat_action", "beatId": "beat_4", "action": "indicate", "targets": ["title_1"]},
        ("beat_4", None),
        {"scene.beats[3]": {"id": "beat_4", "duration": 4, "action": "indicate", "targets": ["title_1"]}},
    ),
    (
        "reference",
        {"op": "set_beat_targets", "beatId": "beat_5", "targets": ["plot_1", "title_1"]},
        ("beat_5", None),
        {"scene.beats[4].targets": ["plot_1", "title_1"]},
    ),
    (
        "reference",
        {"op": "set_beat_param", "beatId": "beat_6", "key": "shift", "value": [0, 1]},
        ("beat_6", None),
        {"scene.beats[5].params": {"shift": [0, 1]}},
    ),
    (
        "reference",
        {"op": "set_beat_param", "beatId": "beat_5", "key": "color", "value": "#00FF00"},
        ("beat_5", None),
        {"scene.beats[4].params": {"color": "#00FF00"}},
    ),
    (
        "camera",
        {"op": "set_beat_param", "beatId": "beat_3", "key": "zoom", "value": 2},
        ("beat_3", None),
        {"scene.beats[2].params": {"phi": 65, "theta": 45, "zoom": 2}},
    ),
    (
        "reference",
        {"op": "set_beat_easing", "beatId": "beat_1", "easing": "linear"},
        ("beat_1", None),
        {"scene.beats[0].easing": "linear"},
    ),
    (
        # Null goes back to the default, which is left out.
        "camera",
        {"op": "set_beat_easing", "beatId": "beat_3", "easing": None},
        ("beat_3", None),
        {
            "scene.beats[2]": {
                "id": "beat_3",
                "duration": 1,
                "action": "move_camera",
                "targets": [],
                "params": {"phi": 65, "theta": 45, "zoom": 1},
            }
        },
    ),
    (
        "reference",
        {"op": "delete_beat", "beatId": "beat_7"},
        ("beat_8", None),
        {"scene.beats[6].id": "beat_8"},
    ),
    (
        "reference",
        {"op": "move_beat", "beatId": "beat_4", "toIndex": 6},
        ("beat_4", None),
        {"scene.beats[3].id": "beat_5", "scene.beats[6].id": "beat_4", "scene.beats[7].id": "beat_8"},
    ),
    (
        "reference",
        {"op": "add_shape", "kind": "dot", "afterBeatId": None},
        ("beat_9", "dot_1"),
        {
            "scene.objects[3]": {
                "id": "dot_1",
                "name": "Dot",
                "type": "dot",
                "params": {"center": [0, 0], "radius": 0.08, "color": "#E07A5F"},
            },
            "scene.beats[8]": {"id": "beat_9", "duration": 2, "action": "grow", "targets": ["dot_1"]},
        },
    ),
    (
        "reference",
        {"op": "add_shape", "kind": "axes_point", "afterBeatId": "beat_2", "axesId": "axes_1"},
        ("beat_9", "point_1"),
        {
            # The middle of the axes' x range [-6.5, 6.5] and y range [-1.5, 1.5].
            "scene.objects[3].params": {"axesId": "axes_1", "x": 0, "y": 0, "radius": 0.08, "color": "#E07A5F"},
            "scene.beats[2]": {"id": "beat_9", "duration": 2, "action": "grow", "targets": ["point_1"]},
        },
    ),
    (
        "shapes",
        {"op": "set_shape_param", "objectId": "circle_1", "key": "radius", "value": 1.5},
        (None, "circle_1"),
        {"scene.objects[4].params.radius": 1.5},
    ),
    (
        "shapes",
        {"op": "set_shape_param", "objectId": "line_1", "key": "end", "value": [3, 1]},
        (None, "line_1"),
        {"scene.objects[5].params": {"start": [-2, 0], "end": [3, 1], "color": "#FFFFFF", "strokeWidth": 4}},
    ),
    (
        "shapes",
        {"op": "set_shape_param", "objectId": "rectangle_1", "key": "color", "value": "#81B29A"},
        (None, "rectangle_1"),
        {"scene.objects[7].params.color": "#81B29A"},
    ),
    (
        "two_axes",
        {"op": "set_shape_param", "objectId": "point_1", "key": "axesId", "value": "axes_2"},
        (None, "point_1"),
        {"scene.objects[5].params": {"axesId": "axes_2", "x": 0, "y": 0, "radius": 0.08, "color": "#E07A5F"}},
    ),
]
RESULT_IDS = [f"{start}-{edit['op']}" for start, edit, _focus, _values in RESULTS]


def test_the_fixture_names_every_op() -> None:
    assert set(OPS) == set(session_module._EDITS)
    assert all(op["examples"] for op in OPS.values())
    assert {start for start, _edit in EXAMPLES} == set(FIXTURE["starts"])


def test_every_example_of_a_beat_or_shape_op_has_its_result() -> None:
    ops = {edit["op"] for _start, edit, _focus, _values in RESULTS}
    listed = [(start, edit) for start, edit, _focus, _values in RESULTS]
    assert [(start, edit) for start, edit in EXAMPLES if edit["op"] in ops] == listed


def test_the_enums_are_the_workers() -> None:
    enums = FIXTURE["enums"]
    assert set(enums["anchor"]) == ANCHORS
    assert enums["format"] == list(FORMATS)
    assert enums["action"] == list(KINDS)
    assert enums["easing"] == list(EASINGS)
    assert enums["shape"] == list(SHAPES)
    assert enums["axis"] == ["x", "y", "z"]
    # set_length takes x or y; set_axes_range also takes z, on 3D axes.
    assert enums["length_axis"] == ["x", "y"]
    assert enums["three_d_mode"] == ["surface", "curve"]
    assert enums["camera_key"] == ["phi", "theta", "zoom"]


@pytest.mark.parametrize(("start", "edit"), EXAMPLES, ids=EXAMPLE_IDS)
def test_every_example_matches_the_fields_of_its_op(start: str, edit: dict[str, Any]) -> None:
    assert start in FIXTURE["starts"]
    fields: dict[str, str] = OPS[edit["op"]]["fields"]
    assert set(edit) - {"op"} <= set(fields), edit
    for name, grammar in fields.items():
        if name not in edit:
            assert grammar.endswith("?"), (edit, name)
            continue
        assert _matches(edit[name], grammar.removesuffix("?")), (edit, name, grammar)


@pytest.mark.parametrize(("start", "edit"), EXAMPLES, ids=EXAMPLE_IDS)
def test_every_example_is_one_undo_step_that_survives_reading_back(start: str, edit: dict[str, object]) -> None:
    session = Session(_document(start))
    before = dumps_project(session.project)
    result = session.edit(edit)
    saved = dumps_project(session.project)
    assert saved != before
    assert dumps_project(load_text(saved)) == saved
    scene = session.project.scene
    assert result.focus_beat_id in {None, *(beat.id for beat in scene.beats)}
    assert result.focus_object_id in {None, *(item.id for item in scene.objects)}
    session.undo()
    assert dumps_project(session.project) == before
    assert not session.can_undo()


@pytest.mark.parametrize(("start", "edit", "focus", "values"), RESULTS, ids=RESULT_IDS)
def test_a_beat_or_shape_example_leaves_what_it_says(
    start: str, edit: dict[str, object], focus: tuple[str | None, str | None], values: dict[str, Any]
) -> None:
    session = Session(_document(start))
    result = session.edit(edit)
    saved = json.loads(dumps_project(session.project))
    assert (result.focus_beat_id, result.focus_object_id) == focus
    assert {path: _at(saved, path) for path in values} == values


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
    """The start document the fixture names: a fixture project or an empty one, with its setup edits."""
    setup = FIXTURE["starts"][start]
    fixture = setup["fixture"]
    path = REPO / "tests" / "fixtures" / str(fixture) / "project.json"
    session = Session(empty_project() if fixture is None else load_text(path.read_text(encoding="utf-8")))
    for edit in setup["edits"]:
        session.edit(edit)
    return session.project


def _at(document: Any, path: str) -> Any:
    """The value at a JSON path such as ``scene.beats[3].params``."""
    for key, index in re.findall(r"(\w+)|\[(\d+)\]", path):
        document = document[key] if key else document[int(index)]
    return document


def _matches(value: object, grammar: str) -> bool:
    """Whether ``value`` has the type ``grammar`` names, in the fixture's field grammar."""
    if grammar.endswith("|null"):
        return value is None or _matches(value, grammar.removesuffix("|null"))
    if grammar.startswith("enum:"):
        return value in FIXTURE["enums"][grammar.removeprefix("enum:")]
    if grammar.endswith("[]"):
        return isinstance(value, list) and all(_matches(item, grammar[:-2]) for item in value)
    sized = re.fullmatch(r"(number|integer)\[(\d+)\]", grammar)
    if sized is not None:
        return (
            isinstance(value, list) and len(value) == int(sized[2]) and all(_matches(item, sized[1]) for item in value)
        )
    if grammar == "param":
        return any(_matches(value, kind) for kind in ("number", "number[2]", "color", "id"))
    checks = {
        "string": lambda: isinstance(value, str),
        "id": lambda: isinstance(value, str) and value != "",
        "color": lambda: isinstance(value, str) and _COLOR.fullmatch(value) is not None,
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "object": lambda: isinstance(value, dict),
    }
    return checks[grammar]()
