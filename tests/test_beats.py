"""The beat catalog, the easings and the shapes (``beats.py``), and how a document stores them.

The table is the one source that parsing, the timeline, codegen, the edits and
the UI read. ``tests/fixtures/protocol/beat-kinds.json`` is the UI's copy of
it; ``uv run pytest tests/test_beats.py --update-golden`` rewrites the file.
Every refusal here is checked on the load path, with its message and field.
No test here starts Manim.
"""

from __future__ import annotations

import json

import pytest

from mathimator_worker.beats import (
    ALL_EASINGS,
    AXES_CHILD_TYPES,
    EASINGS,
    FAMILIES,
    FAMILY_STATES,
    KINDS,
    MIRROR,
    NATURAL_ENTRANCE,
    OBJECT_TYPE_ORDER,
    PATH,
    RETURN,
    SHAPES,
    TWO_D_TYPES,
    BeatKind,
    Param,
    Shape,
)
from mathimator_worker.document import (
    ACTIONS,
    ENTRANCES,
    OBJECT_TYPES,
    THREE_D_OBJECT_TYPES,
    TWO_D_OBJECT_TYPES,
    dumps_project,
    parse_param,
    quantize,
)
from mathimator_worker.identity import beat_cache_keys
from mathimator_worker.jsonio import JsonValue, dumps_document
from tests.beat_documents import (
    Raw,
    axes,
    beat,
    document,
    load,
    refused,
    shape,
    text,
    three_d,
)
from tests.support import REPO

GOLDEN = REPO / "tests" / "fixtures" / "protocol" / "beat-kinds.json"
EDIT_OPS = REPO / "tests" / "fixtures" / "protocol" / "edit-ops.json"
# The actions every document written before the catalog uses, with their generated code unchanged.
LEGACY_ACTIONS = ("wait", "create", "fade_in", "write", "set_color", "move", "transform_formula")


# The UI's copy of the table


def catalog() -> dict[str, JsonValue]:
    """The table as ``beat-kinds.json`` holds it. Codegen-only fields stay in Python."""
    return {
        "easings": [{"name": item.name, "label": item.label, "endsAt": item.ends_at} for item in EASINGS.values()],
        "families": [
            {"name": name, "label": label, "order": order} for order, (name, label) in enumerate(FAMILIES.items())
        ],
        "kinds": [_kind(kind) for kind in KINDS.values()],
        "shapes": [
            {
                "type": item.type,
                "label": item.label,
                "idPrefix": item.id_prefix,
                "entrance": item.entrance,
                "params": [_param(param) for param in item.params],
            }
            for item in SHAPES.values()
        ],
        "naturalEntrance": dict[str, JsonValue](NATURAL_ENTRANCE),
    }


def test_the_ui_copy_of_the_catalog_is_current(pytestconfig: pytest.Config) -> None:
    text_now = dumps_document(catalog())
    if pytestconfig.getoption("update_golden"):
        GOLDEN.write_text(text_now, encoding="utf-8", newline="\n")
        return
    stale = "beat-kinds.json is out of date: rewrite it with uv run pytest tests/test_beats.py --update-golden"
    assert GOLDEN.read_text(encoding="utf-8") == text_now, stale


def _kind(kind: BeatKind) -> dict[str, JsonValue]:
    return {
        "action": kind.action,
        "family": kind.family,
        "label": kind.label,
        "arity": kind.arity,
        "types": _types(kind.types),
        "roles": [
            {
                "name": role.name,
                "label": role.label,
                "types": _types(role.types),
                "before": role.before,
                "after": role.after,
            }
            for role in kind.roles
        ],
        "params": [_param(param) for param in kind.params],
        "easings": [name for name in EASINGS if name in kind.easings],
        "defaultEasing": kind.default_easing,
        "withChildren": kind.with_children,
        "scenes": [scene for scene in ("scene", "three_d_scene") if scene in kind.scenes],
    }


def _param(param: Param) -> dict[str, JsonValue]:
    return {
        "name": param.name,
        "label": param.label,
        "type": param.type,
        "default": param.default,
        "low": param.low,
        "high": param.high,
        "positive": param.positive,
    }


def _types(types: frozenset[str]) -> list[JsonValue]:
    return [item for item in OBJECT_TYPE_ORDER if item in types]


# The table


def test_the_catalog_lists_every_action_in_the_order_the_edit_fixture_names() -> None:
    enums = json.loads(EDIT_OPS.read_text(encoding="utf-8"))["enums"]
    assert list(KINDS) == enums["action"]
    assert list(EASINGS) == enums["easing"]
    assert list(SHAPES) == enums["shape"]
    assert frozenset(KINDS) == ACTIONS
    assert set(LEGACY_ACTIONS) <= ACTIONS


def test_the_object_types_are_the_documents() -> None:
    assert sorted(OBJECT_TYPE_ORDER) == sorted(OBJECT_TYPES)
    assert TWO_D_OBJECT_TYPES == TWO_D_TYPES
    assert set(SHAPES) <= TWO_D_OBJECT_TYPES
    assert not TWO_D_OBJECT_TYPES & THREE_D_OBJECT_TYPES
    assert {"function_plot", "axes_point", "surface", "parametric_curve"} == AXES_CHILD_TYPES


@pytest.mark.parametrize("kind", KINDS.values(), ids=list(KINDS))
def test_each_kind_is_consistent(kind: BeatKind) -> None:
    assert kind.family in FAMILIES
    assert kind.scenes and kind.scenes <= {"scene", "three_d_scene"}
    if kind.arity == "some":
        assert kind.family in FAMILY_STATES
        assert kind.verb and kind.types and not kind.roles
        assert kind.types <= set(OBJECT_TYPES)
    elif kind.arity == "pair":
        assert kind.family in {"morph", "change"}
        assert not kind.verb and not kind.types and len(kind.roles) == 2
        assert all(role.verb and role.noun and role.types <= set(OBJECT_TYPES) for role in kind.roles)
    else:
        assert kind.family in {"hold", "camera"}
        assert not kind.verb and not kind.types and not kind.roles
    if kind.family == "morph":
        assert [(role.before, role.after) for role in kind.roles] == [("on", "off"), ("off", "on")]
    if kind.action == "move_along":
        assert [(role.before, role.after) for role in kind.roles] == [("on", "on"), ("on", "on")]


@pytest.mark.parametrize("kind", KINDS.values(), ids=list(KINDS))
def test_each_kind_names_easings_manim_exports(kind: BeatKind) -> None:
    assert kind.easings <= set(EASINGS)
    if kind.action == "wait":
        assert (kind.easings, kind.default_easing, kind.manim_default) == (frozenset(), None, None)
        return
    assert kind.default_easing in kind.easings
    assert kind.manim_default in EASINGS
    # Smooth wherever it can be, except where a linear default keeps every existing scene's frames.
    if kind.action in {"write", "transform_formula"}:
        assert kind.default_easing == kind.manim_default == "linear"
    elif "smooth" in kind.easings:
        assert kind.default_easing == "smooth"
    if kind.reversed_rate:
        assert kind.easings <= set(MIRROR)
    if kind.family in {"entrance", "exit", "morph"}:
        assert kind.easings <= PATH


def test_the_easing_sets() -> None:
    assert ALL_EASINGS == set(EASINGS) == PATH | RETURN
    assert {"there_and_back", "there_and_back_with_pause"} == RETURN
    assert all(MIRROR[MIRROR[name]] == name for name in MIRROR)
    assert KINDS["indicate"].easings == RETURN
    assert not KINDS["circumscribe"].easings & RETURN
    assert {KINDS[name].default_easing for name in ("unwrite", "circumscribe", "flash", "draw_border_then_fill")} == {
        "smooth"
    }


def test_arrows_neither_grow_nor_shrink_from_a_point() -> None:
    for action in ("grow", "spin_in", "shrink"):
        assert "arrow" not in KINDS[action].types
    assert KINDS["grow_arrow"].types == {"arrow"}


def test_the_entrances_of_the_old_checks() -> None:
    assert {"create", "fade_in", "write", "transform_formula", "transform"} <= ENTRANCES
    assert not ENTRANCES & {"move", "set_color", "wait", "fade_out", "move_along"}
    assert KINDS["create"].types == set(OBJECT_TYPES) - {"math_formula", "text"}
    assert KINDS["write"].types == {"math_formula", "text"}


@pytest.mark.parametrize("object_type", OBJECT_TYPE_ORDER)
def test_every_type_has_a_natural_entrance_that_takes_it(object_type: str) -> None:
    kind = KINDS[NATURAL_ENTRANCE[object_type]]
    assert kind.family == "entrance"
    assert object_type in kind.types
    scene = "three_d_scene" if object_type in THREE_D_OBJECT_TYPES else "scene"
    assert scene in kind.scenes


@pytest.mark.parametrize("item", SHAPES.values(), ids=list(SHAPES))
def test_each_shape_enters_naturally(item: Shape) -> None:
    assert item.entrance == NATURAL_ENTRANCE[item.type]
    assert item.type in KINDS[item.entrance].types


def _all_params() -> list[tuple[str, Param]]:
    found = [(f"{kind.action}.{param.name}", param) for kind in KINDS.values() for param in kind.params]
    found += [(f"{item.type}.{param.name}", param) for item in SHAPES.values() for param in item.params]
    return found


@pytest.mark.parametrize(("name", "param"), _all_params(), ids=[name for name, _param in _all_params()])
def test_every_default_passes_its_own_rules(name: str, param: Param) -> None:
    assert (param.low is None) == (param.high is None), name
    if param.default is None:
        return
    assert parse_param(param, param.default, name) == param.default


# Beat params on load


def _with(action: str, params: Raw | None, *, easing: str | None = None) -> Raw:
    """``text_1`` fades in, then a beat of ``action`` on it."""
    return document([text()], [beat("fade_in", "text_1"), beat(action, "text_1", params=params, easing=easing)])


def _bounded_beat_params() -> list[tuple[str, Param]]:
    return [(kind.action, param) for kind in KINDS.values() for param in kind.params if param.low is not None]


def _bound_cases(param: Param) -> list[tuple[float, bool]]:
    assert param.low is not None and param.high is not None
    low, high = param.low, param.high
    # Judged at three decimals: 0.0004 past a bound rounds back onto it.
    return [
        (low, True),
        (high, True),
        (low - 0.0004, True),
        (high + 0.0004, True),
        (low - 0.001, False),
        (high + 0.001, False),
    ]


@pytest.mark.parametrize(
    ("action", "param", "value", "accepted"),
    [
        (action, param, value, accepted)
        for action, param in _bounded_beat_params()
        for value, accepted in _bound_cases(param)
    ],
)
def test_beat_params_are_judged_at_three_decimals(action: str, param: Param, value: float, accepted: bool) -> None:
    stored: JsonValue = [value, 0] if param.type == "vector" else value
    raw = _with(action, {param.name: stored})
    if accepted:
        saved = json.loads(dumps_project(load(raw)))
        # A file keeps a number as written.
        assert saved["scene"]["beats"][1]["params"][param.name] == stored
        return
    field = f"scene.beats[1].params.{param.name}" + ("[0]" if param.type == "vector" else "")
    caught = refused(raw)
    assert (caught.code, caught.field) == ("invalid_document", field)
    assert str(caught) == f"{field} must be from {quantize(param.low or 0)} to {quantize(param.high or 0)}"


@pytest.mark.parametrize(
    ("action", "params", "field", "message"),
    [
        ("scale", {"factor": 0.0094}, "scene.beats[1].params.factor", "must be from 0.01 to 100"),
        ("rotate", {"angle": 3600.001}, "scene.beats[1].params.angle", "must be from -3600 to 3600"),
        ("set_opacity", {"opacity": -0.001}, "scene.beats[1].params.opacity", "must be from 0 to 1"),
        ("move_to", {"point": [0, 100.001]}, "scene.beats[1].params.point[1]", "must be from -100 to 100"),
        ("move_to", {"point": [0]}, "scene.beats[1].params.point", "must be 2 numbers"),
        ("scale", {"factor": "2"}, "scene.beats[1].params.factor", "must be a number"),
        ("set_color", {"color": "red"}, "scene.beats[1].params.color", "must be #RRGGBB"),
        ("scale", {}, "scene.beats[1].params.factor", "is missing"),
        ("scale", {"factor": 2, "about": [0, 0]}, "scene.beats[1].params.about", "is not a known key"),
        ("scale", None, "scene.beats[1].params", "must be an object"),
        ("indicate", {}, "scene.beats[1].params", "is not allowed for indicate"),
    ],
)
def test_beat_params_are_required_known_and_typed(action: str, params: Raw | None, field: str, message: str) -> None:
    caught = refused(_with(action, params))
    assert (caught.code, caught.field, str(caught)) == ("invalid_document", field, f"{field} {message}")


@pytest.mark.parametrize("action", [kind.action for kind in KINDS.values() if kind.params and kind.arity == "some"])
def test_params_are_saved_in_the_order_of_the_table(action: str) -> None:
    params = {param.name: param.default for param in reversed(KINDS[action].params)}
    raw = _with(action, params, easing="linear")
    raw["scene"]["beats"][1] = dict(reversed(list(raw["scene"]["beats"][1].items())))
    saved = json.loads(dumps_project(load(raw)))["scene"]["beats"][1]
    assert list(saved) == ["id", "duration", "action", "targets", "params", "easing"]
    assert list(saved["params"]) == [param.name for param in KINDS[action].params]


def test_the_camera_move_takes_the_rules_of_the_scene_camera() -> None:
    far = beat("move_camera", params={"zoom": 200, "theta": 3690, "phi": 5000})
    saved = json.loads(dumps_project(load(three_d([far]))))["scene"]["beats"][2]
    assert list(saved["params"].items()) == [("phi", 5000), ("theta", 3690), ("zoom", 200)]
    for zoom, message in ((0, "must be greater than 0"), (0.0004, "rounds to 0 at three decimal places")):
        caught = refused(three_d([beat("move_camera", params={"phi": 0, "theta": 0, "zoom": zoom})]))
        field = "scene.beats[2].params.zoom"
        assert (caught.field, str(caught)) == (field, f"{field} {message}")
    caught = refused(three_d([beat("move_camera", params={"phi": 0, "theta": 0})]))
    assert str(caught) == "scene.beats[2].params.zoom is missing"


# Easing on load


@pytest.mark.parametrize(
    ("action", "easing", "message"),
    [
        ("wait", "smooth", "scene.beats[1].easing is not allowed for wait"),
        ("indicate", "bounce", 'scene.beats[1].easing "bounce" is not an easing'),
        ("indicate", 3, "scene.beats[1].easing 3 is not an easing"),
        ("indicate", None, "scene.beats[1].easing null is not an easing"),
        ("indicate", "smooth", 'scene.beats[1].easing "smooth" is not an easing for indicate'),
        ("uncreate", "slow_into", 'scene.beats[1].easing "slow_into" is not an easing for uncreate'),
        ("circumscribe", "there_and_back", 'scene.beats[1].easing "there_and_back" is not an easing for circumscribe'),
        ("fade_out", "there_and_back", 'scene.beats[1].easing "there_and_back" is not an easing for fade_out'),
    ],
)
def test_an_easing_the_kind_does_not_take_is_refused(action: str, easing: object, message: str) -> None:
    raw = document([shape("circle")], [beat("create", "circle_1"), beat(action, "circle_1")])
    if action == "wait":
        raw["scene"]["beats"][1]["targets"] = []
    raw["scene"]["beats"][1]["easing"] = easing
    caught = refused(raw)
    assert (caught.code, caught.field, str(caught)) == ("invalid_document", "scene.beats[1].easing", message)


@pytest.mark.parametrize("easing", list(EASINGS))
def test_every_easing_of_a_kind_is_kept_as_written(easing: str) -> None:
    raw = _with("scale", {"factor": 2}, easing=easing)
    assert json.loads(dumps_project(load(raw)))["scene"]["beats"][1]["easing"] == easing


def test_a_file_may_name_the_default_easing() -> None:
    raw = document([text()], [beat("fade_in", "text_1", easing="smooth"), beat("unwrite", "text_1")])
    saved = json.loads(dumps_project(load(raw)))["scene"]["beats"]
    assert saved[0]["easing"] == "smooth"
    assert "easing" not in saved[1]


def test_an_easing_changes_the_key_of_its_beat_only() -> None:
    plain = load(_with("scale", {"factor": 2}))
    eased = load(_with("scale", {"factor": 2}, easing="linear"))
    for profile in ("preview", "export"):
        before, after = beat_cache_keys(plain, profile), beat_cache_keys(eased, profile)
        assert before[0] == after[0]
        assert before[1] != after[1]


@pytest.mark.parametrize("name", list(EASINGS))
def test_an_easing_name_is_not_an_object_id(name: str) -> None:
    raw = document([text(name)], [beat("fade_in", name)])
    caught = refused(raw)
    field = "scene.objects[0].id"
    assert (caught.field, str(caught)) == (field, f"{field} {name!r} is not a valid generated name")


# Shapes


@pytest.mark.parametrize("shape_type", list(SHAPES))
def test_each_shape_loads_and_saves_its_params_in_order(shape_type: str) -> None:
    item = shape(shape_type)
    item["params"] = dict(reversed(list(item["params"].items())))
    if "color" in item["params"]:
        item["params"]["color"] = item["params"]["color"].lower()
    objects = [axes(), item] if shape_type == "axes_point" else [item]
    beats = [beat("create", "axes_1")] if shape_type == "axes_point" else []
    beats.append(beat(SHAPES[shape_type].entrance, item["id"]))
    saved = json.loads(dumps_project(load(document(objects, beats))))["scene"]["objects"][-1]
    assert list(saved["params"]) == [param.name for param in SHAPES[shape_type].params]
    assert saved["params"]["color"] == item["params"]["color"].upper()


def _bounded_shape_params() -> list[tuple[str, Param]]:
    return [(item.type, param) for item in SHAPES.values() for param in item.params if param.low is not None]


@pytest.mark.parametrize(
    ("shape_type", "param", "value", "accepted"),
    [
        (shape_type, param, value, accepted)
        for shape_type, param in _bounded_shape_params()
        for value, accepted in _bound_cases(param)
    ],
)
def test_shape_params_are_judged_at_three_decimals(shape_type: str, param: Param, value: float, accepted: bool) -> None:
    stored: JsonValue = [value, 0.5] if param.type == "vector" else value
    # A vector's y of 0.5 keeps a line's start and end apart.
    item = shape(shape_type, **{param.name: stored})
    objects = [axes(), item] if shape_type == "axes_point" else [item]
    beats = [beat("create", "axes_1")] if shape_type == "axes_point" else []
    beats.append(beat(SHAPES[shape_type].entrance, item["id"]))
    raw = document(objects, beats)
    if accepted:
        assert json.loads(dumps_project(load(raw)))["scene"]["objects"][-1]["params"][param.name] == stored
        return
    index = len(objects) - 1
    field = f"scene.objects[{index}].params.{param.name}" + ("[0]" if param.type == "vector" else "")
    caught = refused(raw)
    assert (caught.code, caught.field) == ("invalid_document", field)
    assert str(caught) == f"{field} must be from {quantize(param.low or 0)} to {quantize(param.high or 0)}"


@pytest.mark.parametrize("shape_type", ["line", "arrow"])
@pytest.mark.parametrize(("end", "accepted"), [([2, 1], False), ([2.0004, 1], False), ([2.001, 1], True)])
def test_a_line_or_arrow_needs_two_points(shape_type: str, end: list[float], accepted: bool) -> None:
    raw = document([shape(shape_type, start=[2, 1], end=end)], [beat(SHAPES[shape_type].entrance, f"{shape_type}_1")])
    if accepted:
        load(raw)
        return
    caught = refused(raw)
    field = "scene.objects[0].params.end"
    assert (caught.field, str(caught)) == (field, f"{field} must differ from start")


@pytest.mark.parametrize(
    ("axes_id", "message"),
    [
        ("axes_2", "scene.objects[2].params.axesId must name a axes object"),
        ("text_1", "scene.objects[2].params.axesId must name a axes object"),
        ("Axes", 'scene.objects[2].params.axesId "Axes" is not a valid id'),
    ],
)
def test_a_point_on_axes_names_2d_axes(axes_id: str, message: str) -> None:
    objects = [axes(), text(), shape("axes_point", axesId=axes_id)]
    beats = [beat("create", "axes_1"), beat("fade_in", "text_1"), beat("grow", "point_1")]
    caught = refused(document(objects, beats))
    assert (caught.field, str(caught)) == (message.split(" ")[0], message)


@pytest.mark.parametrize(
    ("shape_type", "missing"),
    [(shape_type, param.name) for shape_type, item in SHAPES.items() for param in item.params],
)
def test_every_shape_param_is_required(shape_type: str, missing: str) -> None:
    item = shape(shape_type)
    del item["params"][missing]
    caught = refused(
        document([axes(), item], [beat("create", "axes_1"), beat(SHAPES[shape_type].entrance, item["id"])])
    )
    field = f"scene.objects[1].params.{missing}"
    assert (caught.field, str(caught)) == (field, f"{field} is missing")


def test_a_shape_param_of_another_shape_is_unknown() -> None:
    caught = refused(document([shape("dot", fillOpacity=0.5)], [beat("grow", "dot_1")]))
    assert str(caught) == "scene.objects[0].params.fillOpacity is not a known key"


def test_a_3d_scene_holds_no_shape() -> None:
    raw = three_d([])
    raw["scene"]["objects"].append(shape("dot"))
    raw["scene"]["beats"].append({"id": "beat_3", **beat("grow", "dot_1")})
    caught = refused(raw)
    field = "scene.objects[2]"
    assert (caught.field, str(caught)) == (field, f"{field} dot_1 is in a 3D scene, which only holds the 3D template")


def test_start_is_still_refused_where_a_row_does_not_allow_it() -> None:
    caught = refused(document([shape("circle", start=[0, 0])], [beat("create", "circle_1")]))
    field = "scene.objects[0].params.start"
    assert (caught.field, str(caught)) == (field, f"{field} is not allowed: beat starts are derived from durations")
