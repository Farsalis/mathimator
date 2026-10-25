"""The timeline rules (``timeline.py``): what is on screen before each beat, re-entry, the axes rule and the camera.

Validation plays the beats out on screen. An entrance needs its targets off
screen and an exit, a change or an emphasis needs them on; a morph takes its
source off and brings its destination on; an object may leave and come back;
what is drawn on axes is on screen only while its axes are. Each rule is
checked on the load path with its message and field. The canonical target
order is checked against ``tests/fixtures/beats/canonical-order.json``, which
the UI's copy of the rule reads too. No test here starts Manim.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mathimator_worker.beats import KINDS, NATURAL_ENTRANCE, BeatKind
from mathimator_worker.document import SceneObject, empty_project, load_text
from mathimator_worker.session import Session
from mathimator_worker.timeline import canonical_targets, simulate
from tests.beat_documents import Raw, axes, beat, document, formula, load, plot, refused, shape, text, three_d
from tests.support import REPO

REFERENCE_60 = REPO / "tests" / "fixtures" / "reference-60" / "project.json"
CANONICAL_ORDER = REPO / "tests" / "fixtures" / "beats" / "canonical-order.json"
# The object a test puts under a kind: the first of these the kind accepts.
SAMPLE_TYPES = ("circle", "text", "dot", "arrow", "line", "rectangle", "axes", "math_formula")
TWO_D_KINDS = [kind for kind in KINDS.values() if "scene" in kind.scenes]
SOME_KINDS = [kind for kind in TWO_D_KINDS if kind.arity == "some"]


def _sample_type(types: frozenset[str]) -> str:
    return next(item for item in SAMPLE_TYPES if item in types)


def _object(object_type: str) -> Raw:
    if object_type == "text":
        return text()
    if object_type == "math_formula":
        return formula()
    if object_type == "axes":
        return axes()
    return shape(object_type)


def _defaults(kind: BeatKind) -> Raw | None:
    return {param.name: param.default for param in kind.params} if kind.params else None


def _ids(kinds: list[BeatKind]) -> list[str]:
    return [kind.action for kind in kinds]


def _assert_refused(raw: Raw, field: str, message: str) -> None:
    caught = refused(raw)
    assert (caught.code, caught.field, str(caught)) == ("invalid_document", field, message)


# Every kind is accepted where its rules hold


def _accepted_document(kind: BeatKind) -> tuple[Raw, list[list[str]]]:
    """A smallest document in which a beat of ``kind`` is accepted, and what is on screen around it."""
    if kind.action == "wait":
        return document([text()], [beat("fade_in", "text_1"), beat("wait")]), [[], ["text_1"], ["text_1"]]
    if kind.action == "move_along":
        beats = [beat("grow", "dot_1"), beat("create", "circle_1"), beat("move_along", "dot_1", "circle_1")]
        both = ["dot_1", "circle_1"]
        return document([shape("dot"), shape("circle")], beats), [[], ["dot_1"], both, both]
    if kind.action == "transform":
        beats = [beat("create", "circle_1"), beat("transform", "circle_1", "rectangle_1")]
        return document([shape("circle"), shape("rectangle")], beats), [[], ["circle_1"], ["rectangle_1"]]
    if kind.action == "transform_formula":
        beats = [beat("write", "eq_1"), beat("transform_formula", "eq_1", "eq_2")]
        return document([formula(), formula("eq_2", "y")], beats), [[], ["eq_1"], ["eq_2"]]
    item = _object(_sample_type(kind.types))
    object_id = item["id"]
    if kind.family == "entrance":
        return document([item], [beat(kind.action, object_id)]), [[], [object_id]]
    beats = [beat(NATURAL_ENTRANCE[item["type"]], object_id), beat(kind.action, object_id, params=_defaults(kind))]
    after = [] if kind.family == "exit" else [object_id]
    return document([item], beats), [[], [object_id], after]


@pytest.mark.parametrize("kind", TWO_D_KINDS, ids=_ids(TWO_D_KINDS))
def test_each_kind_is_accepted_where_its_rules_hold(kind: BeatKind) -> None:
    raw, on_screen = _accepted_document(kind)
    assert simulate(load(raw)).on_screen == on_screen


def test_the_camera_move_is_accepted_in_a_3d_scene() -> None:
    camera = beat("move_camera", params={"phi": 75, "theta": 45, "zoom": 0.8})
    assert simulate(load(three_d([camera]))).on_screen == [
        [],
        ["axes_1"],
        ["axes_1", "surface_1"],
        ["axes_1", "surface_1"],
    ]


def test_several_objects_act_together() -> None:
    objects = [text(), shape("circle"), shape("dot")]
    beats = [
        beat("fade_in", "dot_1", "text_1"),
        beat("create", "circle_1"),
        beat("scale", "circle_1", "text_1", "dot_1", params={"factor": 2}),
        beat("indicate", "text_1", "dot_1"),
        beat("fade_out", "text_1", "circle_1"),
    ]
    assert simulate(load(document(objects, beats))).on_screen == [
        [],
        ["text_1", "dot_1"],
        ["text_1", "circle_1", "dot_1"],
        ["text_1", "circle_1", "dot_1"],
        ["text_1", "circle_1", "dot_1"],
        ["dot_1"],
    ]


# Scene kind, count, names, repeats and types


_TWO_D_ONLY = [kind for kind in TWO_D_KINDS if "three_d_scene" not in kind.scenes]


@pytest.mark.parametrize("kind", _TWO_D_ONLY, ids=_ids(_TWO_D_ONLY))
def test_a_2d_kind_is_refused_in_a_3d_scene(kind: BeatKind) -> None:
    targets = ["axes_1", "surface_1"] if kind.arity == "pair" else ["surface_1"]
    raw = three_d([beat(kind.action, *targets, params=_defaults(kind))])
    _assert_refused(raw, "scene.beats[2].action", f"scene.beats[2].action {kind.action} is not available in a 3D scene")


def test_the_camera_move_is_refused_in_a_2d_scene() -> None:
    raw = document([text()], [beat("fade_in", "text_1"), beat("move_camera", params={"phi": 0, "theta": 0, "zoom": 1})])
    _assert_refused(raw, "scene.beats[1].action", "scene.beats[1].action move_camera is only for a 3D scene")


@pytest.mark.parametrize("kind", SOME_KINDS, ids=_ids(SOME_KINDS))
def test_a_kind_of_one_or_more_needs_a_target(kind: BeatKind) -> None:
    raw = document([text()], [beat("fade_in", "text_1"), beat(kind.action, params=_defaults(kind))])
    field = "scene.beats[1].targets"
    _assert_refused(raw, field, f"{field} must name at least one object for {kind.action}")


def test_a_wait_and_a_camera_move_take_no_target() -> None:
    field = "scene.beats[1].targets"
    _assert_refused(
        document([text()], [beat("fade_in", "text_1"), beat("wait", "text_1")]),
        field,
        f"{field} must be empty for wait",
    )
    camera = beat("move_camera", "axes_1", params={"phi": 0, "theta": 0, "zoom": 1})
    _assert_refused(three_d([camera]), "scene.beats[2].targets", "scene.beats[2].targets must be empty for move_camera")


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("transform", "must be a source and a destination"),
        ("transform_formula", "must be a source and a destination"),
        ("move_along", "must be an object and a path"),
    ],
)
@pytest.mark.parametrize("count", [1, 3])
def test_a_pair_kind_takes_exactly_two(action: str, message: str, count: int) -> None:
    objects = [formula(), formula("eq_2", "y"), formula("eq_3", "z")]
    raw = document(objects, [beat("write", "eq_1"), beat(action, *["eq_1", "eq_2", "eq_3"][:count])])
    _assert_refused(raw, "scene.beats[1].targets", f"scene.beats[1].targets {message}")


def test_a_target_names_an_object_once() -> None:
    field = "scene.beats[0].targets"
    _assert_refused(
        document([text()], [beat("fade_in", "text_1", "ghost_1")]),
        field,
        f"{field} names ghost_1, which is not an object",
    )
    _assert_refused(document([text()], [beat("fade_in", "text_1", "text_1")]), field, f"{field} names text_1 twice")
    morph = [beat("create", "circle_1", "circle_1")]
    _assert_refused(document([shape("circle")], morph), field, f"{field} names circle_1 twice")
    morph = [beat("create", "circle_1"), beat("transform", "circle_1", "circle_1")]
    _assert_refused(
        document([shape("circle")], morph), "scene.beats[1].targets", "scene.beats[1].targets names circle_1 twice"
    )


@pytest.mark.parametrize(
    ("objects", "beats", "message"),
    [
        ([text()], [beat("create", "text_1")], "scene.beats[0] creates text_1, but create is not its entrance"),
        (
            [shape("circle")],
            [beat("write", "circle_1")],
            "scene.beats[0] writes circle_1, but only formulas and text are written",
        ),
        (
            [text(), formula()],
            [beat("write", "text_1"), beat("transform_formula", "text_1", "eq_1")],
            "scene.beats[1] transforms something other than a formula",
        ),
        (
            [formula(), text()],
            [beat("write", "eq_1"), beat("transform_formula", "eq_1", "text_1")],
            "scene.beats[1] transforms something other than a formula",
        ),
    ],
    ids=["create", "write", "transform_formula-source", "transform_formula-destination"],
)
def test_the_kept_type_refusals(objects: list[Raw], beats: list[Raw], message: str) -> None:
    raw = document(objects, beats)
    _assert_refused(raw, f"{message.split(' ')[0]}.targets", message)


_WRONG_TYPES = [
    (kind, object_type)
    for kind in SOME_KINDS
    if kind.type_refusal is None
    for object_type in ("circle", "arrow", "text", "line", "axes")
    if object_type not in kind.types
]


@pytest.mark.parametrize(
    ("kind", "object_type"), _WRONG_TYPES, ids=[f"{kind.action}-{object_type}" for kind, object_type in _WRONG_TYPES]
)
def test_a_kind_that_does_not_apply_to_a_type_says_so(kind: BeatKind, object_type: str) -> None:
    item = _object(object_type)
    raw = document([item], [beat(kind.action, item["id"], params=_defaults(kind))])
    message = f"scene.beats[0] {kind.verb} {item['id']}, but {kind.action} does not apply to {object_type}"
    _assert_refused(raw, "scene.beats[0].targets", message)


def test_the_roles_of_a_move_along_a_path_take_their_types() -> None:
    objects = [shape("circle"), shape("circle", "circle_2"), shape("dot"), shape("dot", "dot_2")]
    entrances = [beat("create", "circle_1", "circle_2"), beat("grow", "dot_1", "dot_2")]
    raw = document(objects, [*entrances, beat("move_along", "circle_1", "circle_2")])
    message = "scene.beats[2] moves circle_1, but the object of move_along cannot be circle"
    _assert_refused(raw, "scene.beats[2].targets", message)
    raw = document(objects, [*entrances, beat("move_along", "dot_1", "dot_2")])
    message = "scene.beats[2] moves along dot_2, but the path of move_along cannot be dot"
    _assert_refused(raw, "scene.beats[2].targets", message)


# On and off screen


def test_an_entrance_needs_its_object_off_screen() -> None:
    raw = document([text()], [beat("fade_in", "text_1"), beat("write", "text_1")])
    _assert_refused(
        raw, "scene.beats[1].targets", "scene.beats[1] brings text_1 on screen while it is already on screen"
    )
    beats = [beat("create", "circle_1", "rectangle_1"), beat("transform", "circle_1", "rectangle_1")]
    message = "scene.beats[1] brings rectangle_1 on screen while it is already on screen"
    _assert_refused(document([shape("circle"), shape("rectangle")], beats), "scene.beats[1].targets", message)


_ON_SCREEN_KINDS = [kind for kind in SOME_KINDS if kind.family != "entrance"]


@pytest.mark.parametrize("kind", _ON_SCREEN_KINDS, ids=_ids(_ON_SCREEN_KINDS))
def test_every_other_kind_needs_its_object_on_screen(kind: BeatKind) -> None:
    item = _object(_sample_type(kind.types))
    object_id = item["id"]
    act = beat(kind.action, object_id, params=_defaults(kind))
    field = "scene.beats[0].targets"
    _assert_refused(document([item], [act]), field, f"scene.beats[0] {kind.verb} {object_id} before it is on screen")
    entrance = beat(NATURAL_ENTRANCE[item["type"]], object_id)
    raw = document([item], [entrance, beat("fade_out", object_id), act])
    message = f"scene.beats[2] {kind.verb} {object_id}, but {object_id} left the screen at scene.beats[1]"
    _assert_refused(raw, "scene.beats[2].targets", message)


def test_a_morph_source_that_left_names_its_transform() -> None:
    beats = [
        beat("create", "circle_1"),
        beat("transform", "circle_1", "rectangle_1"),
        beat("rotate", "circle_1", params={"angle": 90}),
    ]
    message = "scene.beats[2] rotates circle_1, but circle_1 left the screen when it was transformed into rectangle_1"
    _assert_refused(document([shape("circle"), shape("rectangle")], beats), "scene.beats[2].targets", message)


def test_the_roles_of_a_pair_need_their_states() -> None:
    objects = [shape("dot"), shape("circle"), shape("rectangle")]
    path_first = [beat("create", "circle_1"), beat("move_along", "dot_1", "circle_1")]
    message = "scene.beats[1] moves dot_1 before it is on screen"
    _assert_refused(document(objects, path_first), "scene.beats[1].targets", message)
    mover_first = [beat("grow", "dot_1"), beat("move_along", "dot_1", "circle_1")]
    message = "scene.beats[1] moves along circle_1 before it is on screen"
    _assert_refused(document(objects, mover_first), "scene.beats[1].targets", message)
    morph = [beat("transform", "circle_1", "rectangle_1")]
    _assert_refused(
        document(objects, morph), "scene.beats[0].targets", "scene.beats[0] transforms circle_1 before it is on screen"
    )


def test_an_object_must_enter() -> None:
    raw = document([text(), shape("circle")], [beat("fade_in", "text_1")])
    _assert_refused(raw, "scene.objects[1]", "scene.objects[1] circle_1 has no entrance beat")


# Re-entry


_EXITS = [kind for kind in TWO_D_KINDS if kind.family == "exit"]
_ENTRANCES = [kind for kind in TWO_D_KINDS if kind.family == "entrance"]
_RE_ENTRIES = [
    (exit_kind, entrance, object_type)
    for exit_kind in _EXITS
    for entrance in _ENTRANCES
    if (object_type := next((item for item in SAMPLE_TYPES if item in exit_kind.types & entrance.types), None))
]


@pytest.mark.parametrize(
    ("exit_kind", "entrance", "object_type"),
    _RE_ENTRIES,
    ids=[f"{exit_kind.action}-{entrance.action}" for exit_kind, entrance, _type in _RE_ENTRIES],
)
def test_an_object_that_left_can_enter_again(exit_kind: BeatKind, entrance: BeatKind, object_type: str) -> None:
    item = _object(object_type)
    object_id = item["id"]
    beats = [
        beat(NATURAL_ENTRANCE[object_type], object_id),
        beat(exit_kind.action, object_id),
        beat(entrance.action, object_id),
    ]
    assert simulate(load(document([item], beats))).on_screen == [[], [object_id], [], [object_id]]


def test_every_exit_and_entrance_meet_in_a_re_entry() -> None:
    assert {exit_kind.action for exit_kind, _entrance, _type in _RE_ENTRIES} == {kind.action for kind in _EXITS}
    assert {entrance.action for _exit, entrance, _type in _RE_ENTRIES} == {kind.action for kind in _ENTRANCES}


def test_a_morph_source_can_enter_again() -> None:
    beats = [beat("create", "circle_1"), beat("transform", "circle_1", "rectangle_1"), beat("create", "circle_1")]
    on_screen = simulate(load(document([shape("circle"), shape("rectangle")], beats))).on_screen
    assert on_screen == [[], ["circle_1"], ["rectangle_1"], ["circle_1", "rectangle_1"]]
    beats = [beat("write", "eq_1"), beat("transform_formula", "eq_1", "eq_2"), beat("fade_in", "eq_1")]
    on_screen = simulate(load(document([formula(), formula("eq_2", "y")], beats))).on_screen
    assert on_screen == [[], ["eq_1"], ["eq_2"], ["eq_1", "eq_2"]]


def test_a_morph_destination_can_leave_and_morph_back() -> None:
    beats = [
        beat("create", "circle_1"),
        beat("transform", "circle_1", "rectangle_1"),
        beat("transform", "rectangle_1", "circle_1"),
        beat("fade_out", "circle_1"),
    ]
    on_screen = simulate(load(document([shape("circle"), shape("rectangle")], beats))).on_screen
    assert on_screen == [[], ["circle_1"], ["rectangle_1"], ["circle_1"], []]


# Objects drawn on axes


def _graph(*beats: Raw) -> Raw:
    return document([axes(), plot(), shape("circle")], list(beats))


def test_a_plot_needs_its_axes_on_screen_when_it_enters() -> None:
    message = "scene.beats[0] brings plot_1 on screen before its axes axes_1"
    _assert_refused(_graph(beat("create", "plot_1")), "scene.beats[0].targets", message)
    raw = _graph(beat("create", "axes_1"), beat("fade_out", "axes_1"), beat("create", "plot_1"))
    message = "scene.beats[2] brings plot_1 on screen before its axes axes_1"
    _assert_refused(raw, "scene.beats[2].targets", message)


@pytest.mark.parametrize("targets", [("axes_1", "plot_1"), ("plot_1", "axes_1")])
def test_a_plot_may_enter_and_leave_with_its_axes_in_either_order(targets: tuple[str, str]) -> None:
    raw = _graph(
        beat("create", *targets), beat("fade_out", *targets), beat("fade_in", *targets), beat("create", "circle_1")
    )
    graph = ["axes_1", "plot_1"]
    assert simulate(load(raw)).on_screen == [[], graph, [], graph, [*graph, "circle_1"]]


def test_axes_cannot_leave_while_their_plot_is_on_screen() -> None:
    message = "scene.beats[2] takes axes_1 off screen while plot_1, which is drawn on it, is still on screen"
    _assert_refused(
        _graph(beat("create", "axes_1"), beat("create", "plot_1"), beat("uncreate", "axes_1")),
        "scene.beats[2].targets",
        message,
    )
    morph = _graph(beat("create", "axes_1"), beat("create", "plot_1"), beat("transform", "axes_1", "circle_1"))
    _assert_refused(morph, "scene.beats[2].targets", message)


def test_axes_cannot_morph_into_their_own_plot() -> None:
    message = "scene.beats[1] transforms axes_1 into plot_1, but plot_1 is drawn on axes_1"
    _assert_refused(
        _graph(beat("create", "axes_1"), beat("transform", "axes_1", "plot_1")), "scene.beats[1].targets", message
    )


def test_a_point_on_axes_follows_the_axes_rule() -> None:
    objects = [axes(), plot(), shape("axes_point")]
    raw = document(objects, [beat("create", "axes_1", "plot_1"), beat("grow", "point_1"), beat("fade_out", "axes_1")])
    # The first object drawn on the axes, in document order, is named.
    message = "scene.beats[2] takes axes_1 off screen while plot_1, which is drawn on it, is still on screen"
    _assert_refused(raw, "scene.beats[2].targets", message)
    raw = document(
        objects,
        [beat("create", "axes_1", "plot_1"), beat("grow", "point_1"), beat("shrink", "point_1", "axes_1", "plot_1")],
    )
    assert simulate(load(raw)).on_screen[-1] == []
    message = "scene.beats[0] brings point_1 on screen before its axes axes_1"
    _assert_refused(
        document([axes(), shape("axes_point")], [beat("grow", "point_1")]), "scene.beats[0].targets", message
    )


# 3D scenes and the camera


def test_the_3d_template_can_leave_and_come_back() -> None:
    beats = [
        beat("rotate", "axes_1", "surface_1", params={"angle": 30}),
        beat("indicate", "surface_1", "axes_1"),
        beat("shrink", "surface_1", "axes_1"),
        beat("grow", "axes_1", "surface_1"),
        beat("uncreate", "surface_1"),
        beat("fade_in", "surface_1"),
    ]
    both = ["axes_1", "surface_1"]
    assert simulate(load(three_d(beats))).on_screen == [[], ["axes_1"], both, both, both, [], both, ["axes_1"], both]


def test_the_camera_in_effect() -> None:
    beats = [
        beat("move_camera", params={"phi": 75, "theta": 45, "zoom": 0.8}),
        beat("move_camera", params={"phi": 10, "theta": 0, "zoom": 2}, easing="there_and_back"),
        beat("move_camera", params={"phi": 10, "theta": 0, "zoom": 2}, easing="there_and_back_with_pause"),
        beat("wait"),
        beat("move_camera", params={"phi": 20, "theta": -90, "zoom": 1.5}, easing="linear"),
    ]
    cameras = simulate(load(three_d(beats))).cameras
    assert [(camera.phi, camera.theta, camera.zoom) if camera else None for camera in cameras] == [
        (65, -45, 1),
        (65, -45, 1),
        (65, -45, 1),
        (75, 45, 0.8),
        (75, 45, 0.8),
        (75, 45, 0.8),
        (75, 45, 0.8),
        (20, -90, 1.5),
    ]


def test_a_2d_scene_has_no_camera() -> None:
    raw, on_screen = _accepted_document(KINDS["wait"])
    assert simulate(load(raw)).cameras == [None] * len(on_screen)


# The on-screen lists in the state reply


def test_the_state_reply_lists_what_is_on_screen() -> None:
    project = load_text(REFERENCE_60.read_text(encoding="utf-8"))
    every = ["axes_1", "plot_1", "title_1"]
    expected = [[], ["axes_1"], ["axes_1", "plot_1"], *([every] * 6)]
    assert len(expected) == len(project.scene.beats) + 1
    derived: Any = Session(project).state_event("state", "project", None)["derived"]
    assert derived["onScreen"] == expected


def test_an_empty_project_has_nothing_on_screen() -> None:
    derived: Any = Session(empty_project()).state_event("state", "project", None)["derived"]
    assert derived["onScreen"] == [[]]


# Canonical target order


def _canonical_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(CANONICAL_ORDER.read_text(encoding="utf-8"))["cases"]
    return cases


@pytest.mark.parametrize("case", _canonical_cases(), ids=[case["name"] for case in _canonical_cases()])
def test_canonical_target_order(case: dict[str, Any]) -> None:
    project = empty_project()
    project.scene.objects = [
        SceneObject(item["id"], item["id"], item["type"], {"axesId": item["axesId"]} if "axesId" in item else {})
        for item in case["objects"]
    ]
    assert canonical_targets(project, case["action"], case["targets"]) == case["expected"]


def test_canonical_order_keeps_unknown_ids_at_the_end() -> None:
    project = load(_graph(beat("create", "axes_1", "plot_1"), beat("create", "circle_1")))
    assert canonical_targets(project, "fade_out", ["ghost_1", "plot_1", "axes_1"]) == ["axes_1", "plot_1", "ghost_1"]


def test_the_fixture_covers_every_shape_of_the_rule() -> None:
    actions = {case["action"] for case in _canonical_cases()}
    assert {KINDS[action].arity for action in actions} == {"none", "some", "pair"}
    assert {"axes_point", "function_plot", "surface"} <= {
        item["type"] for case in _canonical_cases() for item in case["objects"]
    }
