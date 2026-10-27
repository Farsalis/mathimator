"""The beat and shape edit ops: refusals and their fields, defaults, the no-op rule, and the repair of delete_object.

``add_beat``, ``set_beat_action``, ``set_beat_targets``, ``set_beat_param``,
``set_beat_easing``, ``delete_beat``, ``move_beat``, ``add_shape`` and
``set_shape_param`` each refuse a bad edit field with ``invalid_edit`` naming
that field, and a document the edit would break with the timeline's message
and no field; a refused edit changes nothing. Beat edits store the targets of
a kind of arity "some" in canonical order, and a beat a template owns keeps
its kind and targets. An edit that would not change the saved bytes is a
no-op, which the supervisor answers with ``changed: false``. Deleting an
object repairs the timeline as one undo step. ``test_edit_ops.py`` replays
the fixture's examples of these ops.
"""

from __future__ import annotations

import json

import pytest

from mathimator_worker.beats import KINDS, SHAPES
from mathimator_worker.document import dumps_project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.session import Session
from tests.beat_documents import Raw, axes, beat, document, formula, load, plot, shape, text, three_d
from tests.support import REPO

REFERENCE_60 = REPO / "tests" / "fixtures" / "reference-60" / "project.json"
# The shapes start document of edit-ops.json: every shape added at the end of reference-60.
SHAPE_EDITS: list[dict[str, object]] = [
    {"op": "add_shape", "kind": "dot", "afterBeatId": None},
    {"op": "add_shape", "kind": "circle", "afterBeatId": None},
    {"op": "add_shape", "kind": "line", "afterBeatId": None},
    {"op": "add_shape", "kind": "arrow", "afterBeatId": None},
    {"op": "add_shape", "kind": "rectangle", "afterBeatId": None},
    {"op": "add_shape", "kind": "axes_point", "afterBeatId": None, "axesId": "axes_1"},
]
TEMPLATE_KIND = "beat_1 belongs to the axes_function template, so its kind and targets are set by the template."
TEMPLATE_DELETE = (
    "beat_1 belongs to the axes_function template, so it cannot be deleted. Delete one of its objects instead."
)


def _reference() -> Session:
    """reference-60, which holds axes_1, plot_1 and title_1.

    beat_1 creates axes_1 and beat_2 plot_1 (the axes_function template), beat_3
    writes title_1, beat_4 waits, beat_5 recolors plot_1, beat_6 moves title_1,
    beat_7 waits and beat_8 recolors title_1.
    """
    return Session(load_text(REFERENCE_60.read_text(encoding="utf-8")))


def _shapes() -> Session:
    session = _reference()
    for edit in SHAPE_EDITS:
        session.edit(edit)
    return session


def _surface() -> Session:
    """The 3D template: beat_1 creates axes_1 and beat_2 surface_1, with the camera at phi 65, theta -45, zoom 1."""
    return Session(load(three_d([])))


def _session(raw: Raw) -> Session:
    return Session(load(raw))


def _saved(session: Session) -> Raw:
    saved: Raw = json.loads(dumps_project(session.project))
    return saved


def _beats(session: Session) -> list[Raw]:
    beats: list[Raw] = _saved(session)["scene"]["beats"]
    return beats


def _beat(session: Session, beat_id: str) -> Raw:
    return next(item for item in _beats(session) if item["id"] == beat_id)


def _refused(session: Session, edit: dict[str, object]) -> DocumentError:
    """The refusal of ``edit``, which leaves the document and the history as they were."""
    before = dumps_project(session.project)
    could_undo = session.can_undo()
    with pytest.raises(DocumentError) as caught:
        session.edit(edit)
    assert dumps_project(session.project) == before
    assert session.can_undo() == could_undo
    return caught.value


def _assert_refused(session: Session, edit: dict[str, object], field: str | None, message: str) -> None:
    refusal = _refused(session, edit)
    assert (refusal.code, refusal.field, str(refusal)) == ("invalid_edit", field, message)


def _unchanged(session: Session, edit: dict[str, object]) -> bool:
    """Whether ``edit`` is accepted and leaves the saved bytes as they are: the no-op rule."""
    project, _result = session.edited(edit)
    return dumps_project(project) == dumps_project(session.project)


# Refusals on reference-60, with the edit field they name, or None for a broken document rule

REFERENCE_REFUSALS: list[tuple[dict[str, object], str | None, str]] = [
    # add_beat
    (
        {"op": "add_beat", "afterBeatId": "beat_99", "action": "wait", "targets": []},
        "afterBeatId",
        "unknown beat beat_99",
    ),
    ({"op": "add_beat", "afterBeatId": 5, "action": "wait", "targets": []}, "afterBeatId", "afterBeatId must be an id"),
    ({"op": "add_beat", "afterBeatId": None, "action": "bounce", "targets": []}, "action", "Unknown beat kind bounce."),
    ({"op": "add_beat", "afterBeatId": None, "targets": []}, "action", "Unknown beat kind None."),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "move_camera", "targets": []},
        "action",
        "scene.beats[8].action move_camera is only for a 3D scene",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": "title_1"},
        "targets",
        "targets must be a list of object ids",
    ),
    ({"op": "add_beat", "afterBeatId": None, "action": "indicate"}, "targets", "targets must be a list of object ids"),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": [1]},
        "targets",
        "targets must be a list of object ids",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": ["ghost_1"]},
        "targets",
        "unknown object ghost_1",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "scale", "targets": ["title_1"], "params": [2]},
        "params",
        "params must be an object",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "scale", "targets": ["title_1"], "params": {"size": 2}},
        "params",
        "params.size is not a setting of scale",
    ),
    (
        {
            "op": "add_beat",
            "afterBeatId": None,
            "action": "indicate",
            "targets": ["title_1"],
            "params": {"color": "#FFFFFF"},
        },
        "params",
        "params.color is not a setting of indicate",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "scale", "targets": ["title_1"], "params": {"factor": 0}},
        "params",
        "params.factor must be from 0.01 to 100",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "move_to", "targets": ["title_1"], "params": {"point": 1}},
        "params",
        "params.point must be 2 numbers",
    ),
    (
        {"op": "add_beat", "afterBeatId": "beat_1", "action": "indicate", "targets": ["title_1"]},
        None,
        "scene.beats[1] indicates title_1 before it is on screen",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "fade_in", "targets": ["title_1"]},
        None,
        "scene.beats[8] brings title_1 on screen while it is already on screen",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "wait", "targets": ["title_1"]},
        None,
        "scene.beats[8].targets must be empty for wait",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": []},
        None,
        "scene.beats[8].targets must name at least one object for indicate",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "transform", "targets": ["title_1"]},
        None,
        "scene.beats[8].targets must be a source and a destination",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": ["title_1", "title_1"]},
        None,
        "scene.beats[8].targets names title_1 twice",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "uncreate", "targets": ["title_1"]},
        None,
        "scene.beats[8] uncreates title_1, but uncreate does not apply to math_formula",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "fade_out", "targets": ["axes_1"]},
        None,
        "scene.beats[8] takes axes_1 off screen while plot_1, which is drawn on it, is still on screen",
    ),
    # set_beat_action
    ({"op": "set_beat_action", "beatId": "beat_1", "action": "fade_in"}, "beatId", TEMPLATE_KIND),
    ({"op": "set_beat_action", "beatId": "beat_99", "action": "wait"}, "beatId", "unknown beat beat_99"),
    ({"op": "set_beat_action", "beatId": "beat_4", "action": "bounce"}, "action", "Unknown beat kind bounce."),
    (
        {"op": "set_beat_action", "beatId": "beat_4", "action": "indicate", "targets": "title_1"},
        "targets",
        "targets must be a list of object ids",
    ),
    (
        {"op": "set_beat_action", "beatId": "beat_4", "action": "fade_in", "targets": ["title_1"]},
        None,
        "scene.beats[3] brings title_1 on screen while it is already on screen",
    ),
    (
        {"op": "set_beat_action", "beatId": "beat_6", "action": "move_along"},
        None,
        "scene.beats[5].targets must be an object and a path",
    ),
    # set_beat_targets
    ({"op": "set_beat_targets", "beatId": "beat_1", "targets": ["axes_1"]}, "beatId", TEMPLATE_KIND),
    ({"op": "set_beat_targets", "beatId": "beat_5", "targets": ["ghost"]}, "targets", "unknown object ghost"),
    (
        {"op": "set_beat_targets", "beatId": "beat_5", "targets": None},
        "targets",
        "targets must be a list of object ids",
    ),
    (
        {"op": "set_beat_targets", "beatId": "beat_5", "targets": []},
        None,
        "scene.beats[4].targets must name at least one object for set_color",
    ),
    (
        {"op": "set_beat_targets", "beatId": "beat_4", "targets": ["title_1"]},
        None,
        "scene.beats[3].targets must be empty for wait",
    ),
    # set_beat_param
    ({"op": "set_beat_param", "beatId": "beat_6", "key": "point", "value": [0, 0]}, "key", "move has no setting point"),
    (
        {"op": "set_beat_param", "beatId": "beat_4", "key": "color", "value": "#FFFFFF"},
        "key",
        "wait has no setting color",
    ),
    ({"op": "set_beat_param", "beatId": "beat_6", "value": [0, 0]}, "key", "move has no setting None"),
    ({"op": "set_beat_param", "beatId": "beat_5", "key": "color", "value": "red"}, "value", "value must be #RRGGBB"),
    ({"op": "set_beat_param", "beatId": "beat_6", "key": "shift", "value": [1]}, "value", "value must be 2 numbers"),
    (
        {"op": "set_beat_param", "beatId": "beat_6", "key": "shift", "value": [1, True]},
        "value",
        "value[1] must be a number",
    ),
    ({"op": "set_beat_param", "beatId": "beat_99", "key": "shift", "value": [0, 0]}, "beatId", "unknown beat beat_99"),
    # set_beat_easing
    ({"op": "set_beat_easing", "beatId": "beat_4", "easing": "linear"}, "easing", "wait has no easing"),
    ({"op": "set_beat_easing", "beatId": "beat_5", "easing": "bounce"}, "easing", "bounce is not an easing"),
    ({"op": "set_beat_easing", "beatId": "beat_5", "easing": 5}, "easing", "5 is not an easing"),
    (
        {"op": "set_beat_easing", "beatId": "beat_3", "easing": "there_and_back"},
        "easing",
        "there_and_back is not an easing for write",
    ),
    # delete_beat
    ({"op": "delete_beat", "beatId": "beat_1"}, "beatId", TEMPLATE_DELETE),
    ({"op": "delete_beat", "beatId": "beat_99"}, "beatId", "unknown beat beat_99"),
    ({"op": "delete_beat"}, "beatId", "beatId must be an id"),
    ({"op": "delete_beat", "beatId": "beat_3"}, None, "scene.beats[4] changes title_1 before it is on screen"),
    # move_beat
    ({"op": "move_beat", "beatId": "beat_4", "toIndex": 8}, "toIndex", "toIndex must be from 0 to 7"),
    ({"op": "move_beat", "beatId": "beat_4", "toIndex": -1}, "toIndex", "toIndex must be from 0 to 7"),
    ({"op": "move_beat", "beatId": "beat_4", "toIndex": 1.5}, "toIndex", "toIndex must be from 0 to 7"),
    ({"op": "move_beat", "beatId": "beat_4", "toIndex": True}, "toIndex", "toIndex must be from 0 to 7"),
    ({"op": "move_beat", "beatId": "beat_4", "toIndex": "2"}, "toIndex", "toIndex must be from 0 to 7"),
    ({"op": "move_beat", "beatId": "beat_4"}, "toIndex", "toIndex must be from 0 to 7"),
    (
        {"op": "move_beat", "beatId": "beat_2", "toIndex": 0},
        None,
        "scene.beats[0] brings plot_1 on screen before its axes axes_1",
    ),
    (
        {"op": "move_beat", "beatId": "beat_6", "toIndex": 0},
        None,
        "scene.beats[0] changes title_1 before it is on screen",
    ),
    # add_shape
    ({"op": "add_shape", "kind": "star", "afterBeatId": None}, "kind", "Unknown shape star."),
    ({"op": "add_shape", "afterBeatId": None}, "kind", "Unknown shape None."),
    ({"op": "add_shape", "kind": "dot", "afterBeatId": "beat_99"}, "afterBeatId", "unknown beat beat_99"),
    ({"op": "add_shape", "kind": "axes_point", "afterBeatId": None}, "axesId", "axesId must name an axes object"),
    (
        {"op": "add_shape", "kind": "axes_point", "afterBeatId": None, "axesId": "plot_1"},
        "axesId",
        "axesId must name an axes object",
    ),
    (
        {"op": "add_shape", "kind": "axes_point", "afterBeatId": None, "axesId": ["axes_1"]},
        "axesId",
        "axesId must name an axes object",
    ),
    (
        {"op": "add_shape", "kind": "dot", "afterBeatId": None, "axesId": "axes_1"},
        "axesId",
        "only a point on axes has axesId",
    ),
    # set_shape_param
    (
        {"op": "set_shape_param", "objectId": "plot_1", "key": "radius", "value": 1},
        "objectId",
        "only a shape has radius",
    ),
    ({"op": "set_shape_param", "objectId": "ghost", "key": "radius", "value": 1}, "objectId", "unknown object ghost"),
    # The kept beat aliases of set_beat_param
    ({"op": "set_beat_shift", "beatId": "beat_5", "shift": [0, 1]}, "beatId", "beat beat_5 is not move"),
    ({"op": "set_beat_shift", "beatId": "beat_6", "shift": "up"}, "shift", "shift must be 2 numbers"),
    ({"op": "set_beat_color", "beatId": "beat_6", "color": "#FFFFFF"}, "beatId", "beat beat_6 is not set_color"),
    ({"op": "set_beat_color", "beatId": "beat_5", "color": "red"}, "color", "color must be #RRGGBB"),
]


@pytest.mark.parametrize(
    ("edit", "field", "message"),
    REFERENCE_REFUSALS,
    ids=[f"{edit.get('op')}-{message}" for edit, _field, message in REFERENCE_REFUSALS],
)
def test_a_refused_beat_or_shape_edit_names_its_field_and_changes_nothing(
    edit: dict[str, object], field: str | None, message: str
) -> None:
    _assert_refused(_reference(), edit, field, message)


SHAPE_REFUSALS: list[tuple[dict[str, object], str | None, str]] = [
    (
        {"op": "set_shape_param", "objectId": "circle_1", "key": "width", "value": 1},
        "key",
        "circle has no setting width",
    ),
    ({"op": "set_shape_param", "objectId": "circle_1", "value": 1}, "key", "circle has no setting None"),
    (
        {"op": "set_shape_param", "objectId": "circle_1", "key": "radius", "value": 0},
        "value",
        "value must be from 0.001 to 50",
    ),
    (
        {"op": "set_shape_param", "objectId": "circle_1", "key": "fillOpacity", "value": 1.0006},
        "value",
        "value must be from 0 to 1",
    ),
    (
        {"op": "set_shape_param", "objectId": "circle_1", "key": "center", "value": [1]},
        "value",
        "value must be 2 numbers",
    ),
    (
        {"op": "set_shape_param", "objectId": "circle_1", "key": "center", "value": [0, 101]},
        "value",
        "value[1] must be from -100 to 100",
    ),
    (
        {"op": "set_shape_param", "objectId": "circle_1", "key": "color", "value": "blue"},
        "value",
        "value must be #RRGGBB",
    ),
    (
        {"op": "set_shape_param", "objectId": "point_1", "key": "axesId", "value": "Axes 1"},
        "value",
        'value "Axes 1" is not a valid id',
    ),
    (
        {"op": "set_shape_param", "objectId": "line_1", "key": "end", "value": [-2, 0.0004]},
        None,
        "scene.objects[5].params.end must differ from start",
    ),
    (
        {"op": "set_shape_param", "objectId": "point_1", "key": "axesId", "value": "plot_1"},
        None,
        "scene.objects[8].params.axesId must name a axes object",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "grow", "targets": ["arrow_1"]},
        None,
        "scene.beats[14] grows arrow_1, but grow does not apply to arrow",
    ),
    (
        {"op": "add_beat", "afterBeatId": None, "action": "move_along", "targets": ["circle_1", "dot_1"]},
        None,
        "scene.beats[14] moves circle_1, but the object of move_along cannot be circle",
    ),
    (
        {"op": "set_beat_easing", "beatId": "beat_11", "easing": "there_and_back"},
        "easing",
        "there_and_back is not an easing for create",
    ),
    ({"op": "delete_beat", "beatId": "beat_9"}, None, "scene.objects[3] dot_1 has no entrance beat"),
]


@pytest.mark.parametrize(
    ("edit", "field", "message"),
    SHAPE_REFUSALS,
    ids=[f"{edit.get('op')}-{message}" for edit, _field, message in SHAPE_REFUSALS],
)
def test_a_refused_edit_of_a_shape_names_its_field_and_changes_nothing(
    edit: dict[str, object], field: str | None, message: str
) -> None:
    _assert_refused(_shapes(), edit, field, message)


def test_a_3d_scene_refuses_2d_kinds_and_shapes() -> None:
    session = _surface()
    _assert_refused(
        session,
        {"op": "add_beat", "afterBeatId": None, "action": "write", "targets": ["surface_1"]},
        "action",
        "scene.beats[2].action write is not available in a 3D scene",
    )
    _assert_refused(
        session, {"op": "add_shape", "kind": "dot", "afterBeatId": None}, None, "A 3D scene only holds the 3D template."
    )


def test_add_beat_is_refused_past_the_total_limit() -> None:
    session = _reference()
    session.edit({"op": "set_beat_duration", "beatId": "beat_1", "duration": 67.5})
    refusal = _refused(session, {"op": "add_beat", "afterBeatId": None, "action": "wait", "targets": []})
    assert (refusal.code, refusal.field, str(refusal)) == (
        "limit_exceeded",
        None,
        "scene.beats last more than 120 seconds in total",
    )


# add_beat


@pytest.mark.parametrize("action", [kind.action for kind in KINDS.values() if kind.params and kind.family == "change"])
def test_add_beat_starts_a_kind_from_its_default_settings(action: str) -> None:
    session = _reference()
    result = session.edit({"op": "add_beat", "afterBeatId": None, "action": action, "targets": ["title_1"]})
    defaults = {param.name: param.default for param in KINDS[action].params}
    assert _beat(session, "beat_9") == {
        "id": "beat_9",
        "duration": 1,
        "action": action,
        "targets": ["title_1"],
        "params": defaults,
    }
    assert (result.focus_beat_id, result.focus_object_id) == ("beat_9", "title_1")


def test_add_beat_stores_settings_as_codegen_emits_them() -> None:
    session = _reference()
    for action, params in [
        ("rotate", {"angle": 12.34567}),
        ("move_to", {"point": [1.23456, -2.0]}),
        ("set_color", {"color": "#abcdef"}),
    ]:
        session.edit(
            {"op": "add_beat", "afterBeatId": None, "action": action, "targets": ["title_1"], "params": params}
        )
    assert [item.get("params") for item in _beats(session)[8:]] == [
        {"angle": 12.346},
        {"point": [1.235, -2]},
        {"color": "#ABCDEF"},
    ]


def test_add_beat_stores_targets_in_canonical_order_and_focuses_the_first() -> None:
    session = _reference()
    result = session.edit(
        {"op": "add_beat", "afterBeatId": None, "action": "fade_out", "targets": ["title_1", "plot_1", "axes_1"]}
    )
    assert _beat(session, "beat_9")["targets"] == ["axes_1", "plot_1", "title_1"]
    assert (result.focus_beat_id, result.focus_object_id) == ("beat_9", "axes_1")


def test_canonical_order_puts_axes_before_a_point_listed_earlier_in_the_document() -> None:
    session = _reference()
    session.edit({"op": "add_shape", "kind": "axes_point", "afterBeatId": None, "axesId": "axes_1"})
    session.edit({"op": "add_axes_function", "afterBeatId": "beat_1"})
    # point_1 comes before axes_2 in scene.objects, and is now drawn on it.
    session.edit({"op": "set_shape_param", "objectId": "point_1", "key": "axesId", "value": "axes_2"})
    assert [item.id for item in session.project.scene.objects][3:] == ["point_1", "axes_2", "plot_2"]
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": ["point_1", "axes_2"]})
    added = _beats(session)[-1]["id"]
    assert _beat(session, added)["targets"] == ["axes_2", "point_1"]
    session.edit({"op": "set_beat_targets", "beatId": added, "targets": ["point_1", "title_1", "axes_2"]})
    assert _beat(session, added)["targets"] == ["title_1", "axes_2", "point_1"]
    session.edit({"op": "set_beat_action", "beatId": added, "action": "wiggle", "targets": ["point_1", "axes_2"]})
    assert _beat(session, added)["targets"] == ["axes_2", "point_1"]


def test_a_pair_kind_keeps_its_role_order() -> None:
    session = _shapes()
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "move_along", "targets": ["point_1", "plot_1"]})
    assert _beats(session)[-1]["targets"] == ["point_1", "plot_1"]


# The camera beat starts from the camera in effect


def test_a_camera_beat_turns_a_quarter_from_the_camera_in_effect() -> None:
    session = _surface()
    session.edit(
        {
            "op": "add_beat",
            "afterBeatId": None,
            "action": "move_camera",
            "targets": [],
            "params": {"phi": 70, "theta": 10, "zoom": 2},
        }
    )
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "move_camera", "targets": []})
    # Inserted before the first camera beat, a camera beat starts from scene.camera.
    session.edit(
        {"op": "add_beat", "afterBeatId": "beat_2", "action": "move_camera", "targets": [], "params": {"zoom": 2}}
    )
    assert [item["params"] for item in _beats(session)[2:]] == [
        {"phi": 65, "theta": 45, "zoom": 2},
        {"phi": 70, "theta": 10, "zoom": 2},
        {"phi": 70, "theta": 100, "zoom": 2},
    ]


def test_a_camera_beat_after_one_that_comes_back_starts_where_that_one_started() -> None:
    session = _surface()
    session.edit(
        {
            "op": "add_beat",
            "afterBeatId": None,
            "action": "move_camera",
            "targets": [],
            "params": {"phi": 70, "theta": 10, "zoom": 2},
        }
    )
    session.edit({"op": "set_beat_easing", "beatId": "beat_3", "easing": "there_and_back_with_pause"})
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "move_camera", "targets": []})
    assert _beat(session, "beat_4")["params"] == {"phi": 65, "theta": 45, "zoom": 1}


def test_a_camera_beat_is_accepted_however_far_the_camera_turned() -> None:
    session = _surface()
    for key, value in [("phi", 5000), ("theta", 3600), ("zoom", 200)]:
        session.edit({"op": "set_camera", "key": key, "value": value})
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "move_camera", "targets": []})
    assert _beat(session, "beat_3")["params"] == {"phi": 5000, "theta": 3690, "zoom": 200}


def test_a_camera_setting_follows_the_rules_of_the_scene_camera() -> None:
    session = _surface()
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "move_camera", "targets": []})
    session.edit({"op": "set_beat_param", "beatId": "beat_3", "key": "phi", "value": -720.12345})
    assert _beat(session, "beat_3")["params"]["phi"] == -720.123
    for value, message in [(0, "value must be greater than 0"), (0.0004, "value rounds to 0 at three decimal places")]:
        edit = {"op": "set_beat_param", "beatId": "beat_3", "key": "zoom", "value": value}
        _assert_refused(session, edit, "value", message)


# set_beat_action


def test_a_new_kind_starts_from_its_default_settings() -> None:
    session = _reference()
    result = session.edit({"op": "set_beat_action", "beatId": "beat_6", "action": "move_to"})
    assert _beat(session, "beat_6") == {
        "id": "beat_6",
        "duration": 8,
        "action": "move_to",
        "targets": ["title_1"],
        "params": {"point": [0, 0]},
    }
    assert (result.focus_beat_id, result.focus_object_id) == ("beat_6", None)


def test_the_same_kind_keeps_its_settings_and_easing() -> None:
    session = _reference()
    session.edit({"op": "set_beat_easing", "beatId": "beat_6", "easing": "linear"})
    session.edit({"op": "set_beat_action", "beatId": "beat_6", "action": "move", "targets": ["title_1", "plot_1"]})
    assert _beat(session, "beat_6") == {
        "id": "beat_6",
        "duration": 8,
        "action": "move",
        "targets": ["plot_1", "title_1"],
        "params": {"shift": [0, -0.4]},
        "easing": "linear",
    }


@pytest.mark.parametrize(
    ("easing", "action", "kept"),
    [
        ("linear", "scale", "linear"),
        # Indicate only takes a returning easing.
        ("linear", "indicate", None),
        # Indicate's own default, so it is left out.
        ("there_and_back", "indicate", None),
        ("there_and_back", "wiggle", "there_and_back"),
        ("lingering", "fade_out", "lingering"),
        # Uncreate offers only easings with a mirror image.
        ("lingering", "uncreate", None),
    ],
)
def test_a_new_kind_keeps_an_easing_it_allows_and_does_not_take_by_default(
    easing: str, action: str, kept: str | None
) -> None:
    session = _shapes()
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "move", "targets": ["circle_1"]})
    session.edit({"op": "set_beat_easing", "beatId": "beat_15", "easing": easing})
    session.edit({"op": "set_beat_action", "beatId": "beat_15", "action": action})
    assert _beat(session, "beat_15").get("easing") == kept


def test_a_kind_without_targets_drops_them() -> None:
    session = _reference()
    session.edit({"op": "set_beat_easing", "beatId": "beat_6", "easing": "linear"})
    session.edit({"op": "set_beat_action", "beatId": "beat_6", "action": "wait"})
    assert _beat(session, "beat_6") == {"id": "beat_6", "duration": 8, "action": "wait", "targets": []}


def test_a_beat_becomes_a_camera_beat_from_the_camera_in_effect() -> None:
    session = _surface()
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": ["surface_1"]})
    session.edit({"op": "set_beat_action", "beatId": "beat_3", "action": "move_camera"})
    assert _beat(session, "beat_3") == {
        "id": "beat_3",
        "duration": 1,
        "action": "move_camera",
        "targets": [],
        "params": {"phi": 65, "theta": 45, "zoom": 1},
    }


def test_set_beat_action_stores_targets_in_canonical_order() -> None:
    session = _reference()
    session.edit(
        {"op": "set_beat_action", "beatId": "beat_8", "action": "fade_out", "targets": ["title_1", "plot_1", "axes_1"]}
    )
    assert _beat(session, "beat_8") == {
        "id": "beat_8",
        "duration": 8,
        "action": "fade_out",
        "targets": ["axes_1", "plot_1", "title_1"],
    }


# Settings, easings, deleting and moving


def test_settings_are_stored_as_codegen_emits_them() -> None:
    session = _shapes()
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "rotate", "targets": ["circle_1"]})
    session.edit({"op": "set_beat_param", "beatId": "beat_15", "key": "angle", "value": 45.12345})
    session.edit({"op": "set_beat_param", "beatId": "beat_5", "key": "color", "value": "#abcdef"})
    session.edit({"op": "set_shape_param", "objectId": "circle_1", "key": "center", "value": [1.23456, 0.0]})
    session.edit({"op": "set_shape_param", "objectId": "dot_1", "key": "color", "value": "#81b29a"})
    saved = _saved(session)
    assert _beat(session, "beat_15")["params"] == {"angle": 45.123}
    assert _beat(session, "beat_5")["params"] == {"color": "#ABCDEF"}
    assert saved["scene"]["objects"][4]["params"]["center"] == [1.235, 0]
    assert saved["scene"]["objects"][3]["params"]["color"] == "#81B29A"


def test_the_aliases_store_what_set_beat_param_stores() -> None:
    aliased = _reference()
    aliased.edit({"op": "set_beat_color", "beatId": "beat_5", "color": "#81b29a"})
    aliased.edit({"op": "set_beat_shift", "beatId": "beat_6", "shift": [0.12345, 1]})
    direct = _reference()
    direct.edit({"op": "set_beat_param", "beatId": "beat_5", "key": "color", "value": "#81b29a"})
    direct.edit({"op": "set_beat_param", "beatId": "beat_6", "key": "shift", "value": [0.12345, 1]})
    assert dumps_project(aliased.project) == dumps_project(direct.project)
    assert _beat(direct, "beat_6")["params"] == {"shift": [0.123, 1]}


def test_an_easing_is_left_out_when_it_is_the_default() -> None:
    session = _reference()
    session.edit({"op": "set_beat_easing", "beatId": "beat_5", "easing": "rush_into"})
    assert _beat(session, "beat_5")["easing"] == "rush_into"
    session.edit({"op": "set_beat_easing", "beatId": "beat_5", "easing": "smooth"})
    assert "easing" not in _beat(session, "beat_5")
    session.edit({"op": "set_beat_easing", "beatId": "beat_3", "easing": "smooth"})
    session.edit({"op": "set_beat_easing", "beatId": "beat_3", "easing": None})
    assert "easing" not in _beat(session, "beat_3")


def test_a_template_beat_takes_an_easing_a_duration_and_a_new_place() -> None:
    session = _reference()
    session.edit({"op": "set_beat_easing", "beatId": "beat_2", "easing": "rush_from"})
    session.edit({"op": "set_beat_duration", "beatId": "beat_2", "duration": 3})
    session.edit({"op": "move_beat", "beatId": "beat_2", "toIndex": 2})
    assert [item["id"] for item in _beats(session)[:3]] == ["beat_1", "beat_3", "beat_2"]
    assert _beat(session, "beat_2") == {
        "id": "beat_2",
        "duration": 3,
        "action": "create",
        "targets": ["plot_1"],
        "easing": "rush_from",
    }


@pytest.mark.parametrize(
    ("beat_id", "focus", "remaining"),
    [
        ("beat_4", "beat_5", ["beat_1", "beat_2", "beat_3", "beat_5", "beat_6", "beat_7", "beat_8"]),
        ("beat_8", "beat_7", ["beat_1", "beat_2", "beat_3", "beat_4", "beat_5", "beat_6", "beat_7"]),
    ],
)
def test_deleting_a_beat_focuses_the_one_in_its_place_else_the_one_before(
    beat_id: str, focus: str, remaining: list[str]
) -> None:
    session = _reference()
    result = session.edit({"op": "delete_beat", "beatId": beat_id})
    assert [item["id"] for item in _beats(session)] == remaining
    assert (result.focus_beat_id, result.focus_object_id) == (focus, None)


def test_deleting_the_only_beat_focuses_nothing() -> None:
    session = _session(document([], [beat("wait")]))
    result = session.edit({"op": "delete_beat", "beatId": "beat_1"})
    assert _beats(session) == []
    assert (result.focus_beat_id, result.focus_object_id) == (None, None)


def test_deleting_an_exit_is_refused_when_its_object_enters_again() -> None:
    session = _session(
        document(
            [shape("circle")], [beat("create", "circle_1"), beat("fade_out", "circle_1"), beat("fade_in", "circle_1")]
        )
    )
    edit = {"op": "delete_beat", "beatId": "beat_2"}
    _assert_refused(session, edit, None, "scene.beats[1] brings circle_1 on screen while it is already on screen")


def test_moving_a_beat_later_and_earlier() -> None:
    session = _reference()
    session.edit({"op": "move_beat", "beatId": "beat_7", "toIndex": 3})
    session.edit({"op": "move_beat", "beatId": "beat_4", "toIndex": 7})
    assert [item["id"] for item in _beats(session)] == [
        "beat_1",
        "beat_2",
        "beat_3",
        "beat_7",
        "beat_5",
        "beat_6",
        "beat_8",
        "beat_4",
    ]


# add_shape


@pytest.mark.parametrize("shape_type", list(SHAPES))
def test_a_shape_is_added_with_its_defaults_and_its_entrance(shape_type: str) -> None:
    session = _reference()
    session.edit({"op": "set_axes_range", "objectId": "axes_1", "axis": "x", "value": [0, 5, 1]})
    edit: dict[str, object] = {"op": "add_shape", "kind": shape_type, "afterBeatId": "beat_8"}
    if shape_type == "axes_point":
        edit["axesId"] = "axes_1"
    result = session.edit(edit)
    row = SHAPES[shape_type]
    params = {param.name: param.default for param in row.params}
    if shape_type == "axes_point":
        # The middle of the x range [0, 5] and of the y range [-1.5, 1.5].
        params.update(axesId="axes_1", x=2.5, y=0)
    object_id = f"{row.id_prefix}_1"
    saved = _saved(session)
    assert saved["scene"]["objects"][3] == {"id": object_id, "name": row.name, "type": shape_type, "params": params}
    assert saved["scene"]["beats"][8] == {"id": "beat_9", "duration": 2, "action": row.entrance, "targets": [object_id]}
    assert (result.focus_beat_id, result.focus_object_id) == ("beat_9", object_id)


def test_a_point_needs_its_axes_on_screen_where_it_enters() -> None:
    session = _reference()
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "fade_out", "targets": ["axes_1", "plot_1"]})
    edit = {"op": "add_shape", "kind": "axes_point", "afterBeatId": None, "axesId": "axes_1"}
    _assert_refused(session, edit, None, "scene.beats[9] brings point_1 on screen before its axes axes_1")


@pytest.mark.parametrize("axis", ["x", "y"])
def test_a_point_is_refused_on_axes_whose_middle_is_beyond_its_bounds(axis: str) -> None:
    session = _reference()
    session.edit(
        {"op": "set_axes_range", "objectId": "axes_1", "axis": axis, "value": [10_000_000, 20_000_000, 100_000]}
    )
    edit = {"op": "add_shape", "kind": "axes_point", "afterBeatId": None, "axesId": "axes_1"}
    message = f"the middle of the {axis} range of axes_1 must be from -1000000 to 1000000 for a point on axes"
    _assert_refused(session, edit, "axesId", message)


def test_a_point_may_sit_on_the_bound_of_its_coordinates() -> None:
    session = _reference()
    session.edit({"op": "set_axes_range", "objectId": "axes_1", "axis": "x", "value": [0, 2_000_000, 10_000]})
    session.edit({"op": "add_shape", "kind": "axes_point", "afterBeatId": None, "axesId": "axes_1"})
    assert _saved(session)["scene"]["objects"][3]["params"]["x"] == 1_000_000


# The no-op rule: an edit that would not change the saved bytes


@pytest.mark.parametrize(
    "edit",
    [
        {"op": "move_beat", "beatId": "beat_4", "toIndex": 3},
        {"op": "set_beat_easing", "beatId": "beat_5", "easing": None},
        {"op": "set_beat_easing", "beatId": "beat_5", "easing": "smooth"},
        {"op": "set_beat_easing", "beatId": "beat_3", "easing": "linear"},
        {"op": "set_beat_targets", "beatId": "beat_5", "targets": ["plot_1"]},
        {"op": "set_beat_action", "beatId": "beat_5", "action": "set_color"},
        {"op": "set_beat_action", "beatId": "beat_4", "action": "wait"},
        {"op": "set_beat_param", "beatId": "beat_6", "key": "shift", "value": [0, -0.4]},
        {"op": "set_beat_param", "beatId": "beat_5", "key": "color", "value": "#e07a5f"},
    ],
    ids=lambda edit: f"{edit['op']}-{edit['beatId']}",
)
def test_an_edit_to_what_is_there_changes_nothing(edit: dict[str, object]) -> None:
    assert _unchanged(_reference(), edit)


def test_the_same_targets_in_another_order_change_nothing() -> None:
    session = _reference()
    session.edit({"op": "set_beat_targets", "beatId": "beat_8", "targets": ["title_1", "plot_1"]})
    assert _beat(session, "beat_8")["targets"] == ["plot_1", "title_1"]
    assert _unchanged(session, {"op": "set_beat_targets", "beatId": "beat_8", "targets": ["plot_1", "title_1"]})
    assert _unchanged(session, {"op": "set_beat_targets", "beatId": "beat_8", "targets": ["title_1", "plot_1"]})


def test_a_shape_setting_to_its_value_changes_nothing() -> None:
    assert _unchanged(_shapes(), {"op": "set_shape_param", "objectId": "circle_1", "key": "radius", "value": 1})


# delete_object repairs the timeline, as one undo step


def _after_delete(raw: Raw, object_id: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The object ids, and each beat's action and targets, after deleting ``object_id``; undo restores the document."""
    session = _session(raw)
    before = dumps_project(session.project)
    session.edit({"op": "delete_object", "objectId": object_id})
    saved = _saved(session)
    objects = [item["id"] for item in saved["scene"]["objects"]]
    beats = [(item["action"], item["targets"]) for item in saved["scene"]["beats"]]
    session.undo()
    assert dumps_project(session.project) == before
    return objects, beats


def test_deleting_a_morph_destination_drops_a_later_entrance_of_the_source() -> None:
    raw = document(
        [formula(), formula("eq_2", "y")],
        [beat("write", "eq_1"), beat("transform_formula", "eq_1", "eq_2"), beat("fade_in", "eq_1")],
    )
    # Without the transform eq_1 stays on screen, so fading it in again is dropped.
    assert _after_delete(raw, "eq_2") == (["eq_1"], [("write", ["eq_1"])])


def test_deleting_a_morph_source_deletes_the_destination_it_brought_on() -> None:
    raw = document(
        [text(), shape("circle"), shape("rectangle")],
        [
            beat("fade_in", "text_1"),
            beat("create", "circle_1"),
            beat("transform", "circle_1", "rectangle_1"),
            beat("indicate", "rectangle_1", "text_1"),
        ],
    )
    assert _after_delete(raw, "circle_1") == (["text_1"], [("fade_in", ["text_1"]), ("indicate", ["text_1"])])


def test_deleting_one_target_of_an_exit_keeps_the_others() -> None:
    raw = document(
        [shape("circle"), shape("dot")],
        [beat("create", "circle_1"), beat("grow", "dot_1"), beat("fade_out", "circle_1", "dot_1")],
    )
    assert _after_delete(raw, "circle_1") == (["dot_1"], [("grow", ["dot_1"]), ("fade_out", ["dot_1"])])


def test_axes_stay_on_screen_while_a_plot_that_no_longer_leaves_is_on() -> None:
    raw = document(
        [axes(), plot(), shape("circle"), shape("rectangle")],
        [
            beat("create", "axes_1"),
            beat("create", "plot_1"),
            beat("create", "circle_1"),
            beat("transform", "plot_1", "rectangle_1"),
            beat("fade_out", "axes_1", "circle_1"),
        ],
    )
    # Without the transform the plot stays on, so its axes leave the exit and stay with it.
    assert _after_delete(raw, "rectangle_1") == (
        ["axes_1", "plot_1", "circle_1"],
        [("create", ["axes_1"]), ("create", ["plot_1"]), ("create", ["circle_1"]), ("fade_out", ["circle_1"])],
    )


def test_a_point_does_not_enter_while_its_axes_no_longer_do() -> None:
    raw = document(
        [axes(), shape("axes_point"), shape("dot"), shape("circle")],
        [
            beat("create", "axes_1"),
            beat("grow", "point_1"),
            beat("grow", "dot_1"),
            beat("create", "circle_1"),
            beat("fade_out", "axes_1", "point_1", "dot_1"),
            beat("transform", "circle_1", "axes_1"),
            beat("fade_in", "point_1", "dot_1"),
        ],
    )
    # Without the transform the axes stay off, so the point leaves the entrance and the dot enters alone.
    assert _after_delete(raw, "circle_1") == (
        ["axes_1", "point_1", "dot_1"],
        [
            ("create", ["axes_1"]),
            ("grow", ["point_1"]),
            ("grow", ["dot_1"]),
            ("fade_out", ["axes_1", "point_1", "dot_1"]),
            ("fade_in", ["dot_1"]),
        ],
    )


def test_an_object_that_no_beat_brings_on_any_more_is_deleted_with_what_is_drawn_on_it() -> None:
    raw = document(
        [shape("circle"), axes(), plot(), text()],
        [
            beat("fade_in", "text_1"),
            beat("create", "circle_1"),
            beat("transform", "circle_1", "axes_1"),
            beat("create", "plot_1"),
            beat("indicate", "plot_1", "text_1"),
        ],
    )
    assert _after_delete(raw, "circle_1") == (["text_1"], [("fade_in", ["text_1"]), ("indicate", ["text_1"])])


def test_deleting_the_3d_template_leaves_a_2d_scene_without_camera_moves() -> None:
    raw = three_d(
        [beat("move_camera", params={"phi": 70, "theta": 0, "zoom": 2}), beat("wait"), beat("indicate", "surface_1")]
    )
    session = _session(raw)
    session.edit({"op": "delete_object", "objectId": "axes_1"})
    scene = _saved(session)["scene"]
    assert (scene["kind"], "camera" in scene) == ("scene", False)
    assert (scene["objects"], scene["templateInstances"]) == ([], [])
    assert scene["beats"] == [{"id": "beat_4", "duration": 1, "action": "wait", "targets": []}]


def test_deleting_an_object_of_a_template_keeps_the_rest_of_a_shared_beat() -> None:
    session = _reference()
    session.edit({"op": "add_beat", "afterBeatId": None, "action": "indicate", "targets": ["plot_1", "title_1"]})
    session.edit({"op": "delete_object", "objectId": "plot_1"})
    saved = _saved(session)
    assert [item["id"] for item in saved["scene"]["objects"]] == ["axes_1", "title_1"]
    assert saved["scene"]["templateInstances"] == []
    assert [(item["id"], item["targets"]) for item in saved["scene"]["beats"]] == [
        ("beat_1", ["axes_1"]),
        ("beat_3", ["title_1"]),
        ("beat_4", []),
        ("beat_6", ["title_1"]),
        ("beat_7", []),
        ("beat_8", ["title_1"]),
        ("beat_9", ["title_1"]),
    ]
