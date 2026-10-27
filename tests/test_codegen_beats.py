"""The code each beat kind, shape and easing compiles to, and what a beat's cache key holds.

``tests/fixtures/beats/codegen.json`` holds, for every case below, the lines
of its beat blocks: one case per kind, then the forms that several targets,
centres, opacity, easings, re-entry, morphs and shapes take.
``uv run pytest tests/test_codegen_beats.py --update-golden`` rewrites it. The
examples of spec section 5, "Beats, normative output", are checked against the
spec itself. Two documents that use every kind compile with ``py_compile``
and name only what ``from manim import *`` binds. No test here renders.
"""

from __future__ import annotations

import ast
import json
import py_compile
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mathimator_worker.beats import EASINGS, KINDS, MIRROR, NATURAL_ENTRANCE, SHAPES
from mathimator_worker.codegen import generate
from mathimator_worker.identity import beat_cache_keys
from tests.beat_documents import Raw, axes, beat, document, formula, load, plot, shape, text, three_d
from tests.support import REPO

GOLDEN = REPO / "tests" / "fixtures" / "beats" / "codegen.json"
SPEC = REPO / "docs" / "design-spec.md"
# The names the generated code takes from ``from manim import *``.
MANIM_NAMES = frozenset(
    {
        *("Scene", "ThreeDScene", "config", "DEGREES", "ORIGIN", "UP", "DOWN", "LEFT", "RIGHT"),
        *("PURE_YELLOW", "SMALL_BUFF", "VGroup", "Axes", "ThreeDAxes", "Surface", "ParametricFunction"),
        *("MathTex", "Text", "Dot", "Line", "Arrow", "Circle", "Rectangle"),
        *("Create", "FadeIn", "Write", "GrowFromCenter", "GrowArrow", "DrawBorderThenFill", "SpinInFromNothing"),
        *("FadeOut", "Uncreate", "Unwrite", "ShrinkToCenter", "Rotate", "Transform", "Indicate", "Circumscribe"),
        *("Flash", "Wiggle", "FocusOn", "ApplyWave", "MoveAlongPath", "ReplacementTransform"),
        "TransformMatchingTex",
    }
)


def labelled_axes() -> Raw:
    item = axes()
    item["params"]["showLabels"] = True
    return item


def point(**params: Any) -> Raw:
    return shape("axes_point", "point_1", **params)


def title() -> Raw:
    return text("title_1")


def circle() -> Raw:
    return shape("circle")


def then(action: str, item: Raw, params: Raw | None = None) -> Raw:
    """``item`` enters on its natural entrance, then a beat of ``action`` acts on it."""
    entrance = beat(NATURAL_ENTRANCE[item["type"]], item["id"])
    return document([item], [entrance, beat(action, item["id"], params=params)])


def graph(*beats: Raw, labels: bool = False) -> Raw:
    """Axes, a plot, a point on them and a text, which the first three beats bring on, then ``beats``."""
    entrances = [beat("create", "axes_1", "plot_1"), beat("grow", "point_1"), beat("write", "title_1")]
    return document([labelled_axes() if labels else axes(), plot(), point(), title()], [*entrances, *beats])


def every_2d_type(action: str, params: Raw) -> Raw:
    """One object of every 2D type on screen, then one beat of ``action`` on all of them."""
    objects = [
        labelled_axes(),
        plot(),
        point(),
        title(),
        formula(),
        shape("dot"),
        shape("line"),
        shape("arrow"),
        shape("circle"),
        shape("rectangle", fillOpacity=0.3),
    ]
    ids = [item["id"] for item in objects]
    entrances = [
        beat("create", "axes_1", "plot_1"),
        beat("grow", "point_1", "dot_1"),
        beat("write", "title_1", "eq_1"),
        beat("create", "line_1", "circle_1", "rectangle_1"),
        beat("grow_arrow", "arrow_1"),
    ]
    return document(objects, [*entrances, beat(action, *ids, params=params)])


def easings_on(action: str, item: Raw, entrance: str, names: list[str]) -> Raw:
    """``item`` enters, then leaves by ``action`` with each easing of ``names`` in turn."""
    beats: list[Raw] = []
    for name in names:
        beats += [beat(entrance, item["id"]), beat(action, item["id"], easing=name)]
    return document([item], beats)


def three_d_with(*beats: Raw, mode: str = "curve") -> Raw:
    return three_d(list(beats), mode)


def added(shape_type: str, **params: Any) -> Raw:
    """A shape with ``params``, entering as Add brings it in; a point on axes after its axes."""
    item = shape(shape_type, **params)
    entrance = beat(SHAPES[shape_type].entrance, item["id"])
    if shape_type == "axes_point":
        return document([axes(), item], [beat("create", "axes_1"), entrance])
    return document([item], [entrance])


CASES: dict[str, Raw] = {
    # One case per kind.
    "wait": document([circle()], [beat("fade_in", "circle_1"), beat("wait")]),
    "create": document([circle()], [beat("create", "circle_1")]),
    "fade_in": document([text()], [beat("fade_in", "text_1")]),
    "write": document([formula()], [beat("write", "eq_1")]),
    "grow": document([shape("dot")], [beat("grow", "dot_1")]),
    "grow_arrow": document([shape("arrow")], [beat("grow_arrow", "arrow_1")]),
    "draw_border_then_fill": document(
        [shape("rectangle", fillOpacity=0.5)], [beat("draw_border_then_fill", "rectangle_1")]
    ),
    "spin_in": document([shape("rectangle")], [beat("spin_in", "rectangle_1")]),
    "fade_out": then("fade_out", circle()),
    "uncreate": then("uncreate", circle()),
    "unwrite": then("unwrite", text()),
    "shrink": then("shrink", circle()),
    "move": then("move", circle(), {"shift": [1, -0.5]}),
    "move_to": then("move_to", circle(), {"point": [2.5, -1]}),
    "scale": then("scale", circle(), {"factor": 0.75}),
    "rotate": then("rotate", shape("rectangle"), {"angle": -45}),
    "set_opacity": then("set_opacity", text(), {"opacity": 0.25}),
    "set_color": then("set_color", circle(), {"color": "#FF0000"}),
    "move_along": document(
        [circle(), shape("dot")], [beat("fade_in", "circle_1", "dot_1"), beat("move_along", "dot_1", "circle_1")]
    ),
    "indicate": then("indicate", circle()),
    "circumscribe": then("circumscribe", text()),
    "flash": then("flash", shape("dot")),
    "wiggle": then("wiggle", circle()),
    "focus": then("focus", shape("dot")),
    "wave": then("wave", text()),
    "transform": document(
        [circle(), shape("rectangle")], [beat("create", "circle_1"), beat("transform", "circle_1", "rectangle_1")]
    ),
    "transform_formula": document(
        [formula("eq_1", "x"), formula("eq_2", "y")],
        [beat("write", "eq_1"), beat("transform_formula", "eq_1", "eq_2")],
    ),
    "move_camera": three_d_with(beat("move_camera", params={"phi": 75, "theta": 45.5, "zoom": 0.8})),
    # Several targets.
    "an entrance builds axes before the plot it lists first": document(
        [labelled_axes(), plot()], [beat("create", "plot_1", "axes_1")]
    ),
    "an entrance of several objects": document([circle(), title()], [beat("fade_in", "title_1", "circle_1")]),
    "a move of labelled axes, their plot and a point": graph(
        beat("move", "axes_1", "plot_1", "point_1", params={"shift": [0, 1.25]}), labels=True
    ),
    "a recolour of labelled axes leaves their labels": graph(
        beat("set_color", "axes_1", "plot_1", params={"color": "#81B29A"}), labels=True
    ),
    "a move to a point about the shared centre": graph(
        beat("move_to", "axes_1", "plot_1", params={"point": [1, -1]}), labels=True
    ),
    "a scale about the shared centre": graph(
        beat("scale", "axes_1", "plot_1", "point_1", params={"factor": 0.5}), labels=True
    ),
    "a rotation about the shared centre": graph(
        beat("rotate", "point_1", "title_1", params={"angle": 3600}), labels=True
    ),
    "indicate on two separate objects": graph(beat("indicate", "point_1", "title_1")),
    "wiggle on two separate objects": graph(beat("wiggle", "point_1", "title_1")),
    "indicate on an axes cluster and a text": graph(beat("indicate", "axes_1", "plot_1", "title_1"), labels=True),
    "wiggle on an axes cluster and a text": graph(beat("wiggle", "axes_1", "plot_1", "title_1"), labels=True),
    "indicate on axes and a point listed before them": graph(beat("indicate", "point_1", "axes_1")),
    "indicate on axes alone": graph(beat("indicate", "axes_1")),
    "exits of several objects": graph(beat("fade_out", "title_1", "point_1", "plot_1", "axes_1")),
    # Opacity, once per object type.
    "opacity on every 2D type": every_2d_type("set_opacity", {"opacity": 0.5}),
    "opacity on 3D axes and a curve": three_d_with(beat("set_opacity", "axes_1", "curve_1", params={"opacity": 0.4})),
    "opacity on a surface": three_d_with(beat("set_opacity", "surface_1", params={"opacity": 0.4}), mode="surface"),
    # Easings.
    "every easing on a scale": document(
        [circle()],
        [beat("fade_in", "circle_1")]
        + [beat("scale", "circle_1", params={"factor": 2}, easing=name) for name in EASINGS],
    ),
    "uncreate written as the mirror of its easing": easings_on("uncreate", circle(), "create", list(MIRROR)),
    "unwrite written as the mirror of its easing": easings_on("unwrite", text(), "write", list(MIRROR)),
    "a move along a path there and back": graph(
        beat("move_along", "point_1", "plot_1", easing="there_and_back"),
        beat("move_along", "point_1", "plot_1", easing="there_and_back_with_pause"),
    ),
    "a camera move there and back": three_d_with(
        beat("move_camera", params={"phi": 60, "theta": 0, "zoom": 1.5}, easing="there_and_back")
    ),
    # Re-entry and morphs.
    "a re-entry builds the object again from its fields": document(
        [circle()],
        [
            beat("create", "circle_1"),
            beat("set_color", "circle_1", params={"color": "#FFFFFF"}),
            beat("shrink", "circle_1"),
            beat("grow", "circle_1"),
        ],
    ),
    "a plot enters again on axes that moved": graph(
        beat("fade_out", "plot_1", "point_1"),
        beat("move", "axes_1", params={"shift": [-1, 0]}),
        beat("create", "plot_1"),
    ),
    "a transform from labelled axes": document(
        [labelled_axes(), title()], [beat("create", "axes_1"), beat("transform", "axes_1", "title_1")]
    ),
    "a transform into a plot on its axes": document(
        [axes(), plot(), circle()],
        [beat("create", "axes_1", "circle_1"), beat("transform", "circle_1", "plot_1")],
    ),
    "a transformed formula enters again": document(
        [formula("eq_1", "x"), formula("eq_2", "y")],
        [
            beat("write", "eq_1"),
            beat("transform_formula", "eq_1", "eq_2"),
            beat("fade_out", "eq_2"),
            beat("fade_in", "eq_1"),
        ],
    ),
    # Shapes at their fields, each on the entrance Add gives it.
    "a dot at its fields": added("dot", center=[-1.5, 0.25], radius=0.2, color="#F2CC8F"),
    "a line at its fields": added("line", start=[-3, -1], end=[2.5, 1.125], strokeWidth=2.5),
    "an arrow at its fields": added("arrow", start=[0, 0], end=[-2, 3], color="#81B29A"),
    "a circle at its fields": added("circle", center=[1, 1], radius=0.5, strokeWidth=0, fillOpacity=0.4),
    "a rectangle at its fields": added("rectangle", center=[-2, 0], width=1.5, height=3, fillOpacity=1),
    "a point on axes at its fields": added("axes_point", x=-2.5, y=0.75, radius=0.1),
}


def blocks(source: str) -> list[str]:
    """The lines of the beat blocks: everything after the construct header."""
    lines = source.splitlines()
    start = next(index for index, line in enumerate(lines) if "self.camera.background_color" in line) + 1
    if lines[start].lstrip().startswith("self.set_camera_orientation("):
        start += 1
    return lines[start + 1 :]


def compiled(name: str) -> list[str]:
    return blocks(generate(load(CASES[name])))


# The goldens


def test_the_goldens_are_current(pytestconfig: pytest.Config) -> None:
    current = {name: compiled(name) for name in CASES}
    if pytestconfig.getoption("update_golden"):
        GOLDEN.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return
    stale = "codegen.json is out of date: rewrite it with uv run pytest tests/test_codegen_beats.py --update-golden"
    assert list(json.loads(GOLDEN.read_text(encoding="utf-8"))) == list(CASES), stale


@pytest.mark.parametrize("name", CASES)
def test_each_case_compiles_to_its_golden(name: str) -> None:
    assert compiled(name) == json.loads(GOLDEN.read_text(encoding="utf-8"))[name]


def test_every_kind_has_a_case_of_its_own() -> None:
    assert [name for name in CASES if name in KINDS] == list(KINDS)


def test_no_play_holds_a_group() -> None:
    golden: dict[str, list[str]] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    plays = [line for lines in golden.values() for line in lines if "self.play(" in line]
    assert plays
    assert not [line for line in plays if "VGroup(" in line]


# The normative examples of spec section 5


def _normative() -> list[str]:
    """The fenced blocks under "Beats, normative output", in order."""
    spec = SPEC.read_text(encoding="utf-8")
    start = spec.index("### Beats, normative output")
    section = spec[start : spec.index("\n### ", start + 1)]
    return re.findall(r"```python\n(.*?)```", section, re.DOTALL)


def _normative_documents() -> list[tuple[Raw, bool]]:
    """Each example's document, and whether the example is all of its beat blocks."""
    shapes = [shape("dot"), shape("circle")]
    entrances = [beat("grow", "dot_1", duration=2), beat("create", "circle_1", duration=2)]
    opacity = document(
        [axes(), plot(), title(), circle(), shape("rectangle", fillOpacity=0.3)],
        [
            beat("create", "axes_1"),
            beat("create", "plot_1"),
            beat("write", "title_1"),
            beat("create", "circle_1"),
            beat("create", "rectangle_1"),
            beat("set_opacity", "circle_1", "rectangle_1", params={"opacity": 0.5}),
            beat("set_opacity", "plot_1", "title_1", params={"opacity": 0.5}),
            beat("flash", "title_1"),
            beat("uncreate", "circle_1", easing="rush_into"),
            beat("unwrite", "title_1"),
        ],
    )
    return [
        (
            document(
                shapes,
                [
                    *entrances,
                    beat("scale", "dot_1", "circle_1", params={"factor": 1.5}, easing="rush_into"),
                    beat("rotate", "circle_1", params={"angle": 90}),
                    beat("indicate", "dot_1", "circle_1"),
                    beat("fade_out", "dot_1"),
                    beat("fade_in", "dot_1"),
                ],
            ),
            True,
        ),
        (
            document(
                [*shapes, shape("rectangle")],
                [*entrances, beat("transform", "circle_1", "rectangle_1", duration=2)],
            ),
            False,
        ),
        (
            graph(
                beat("move_along", "point_1", "plot_1", easing="linear", duration=2),
                beat("move_along", "point_1", "plot_1", easing="there_and_back", duration=2),
            ),
            False,
        ),
        (three_d_with(beat("move_camera", params={"phi": 75, "theta": 45, "zoom": 0.8}, duration=2)), False),
        (opacity, False),
        (
            graph(
                beat("indicate", "axes_1", "plot_1", "title_1"),
                beat("wiggle", "axes_1", "plot_1", "title_1"),
                labels=True,
            ),
            False,
        ),
    ]


def test_the_spec_examples_are_what_codegen_writes() -> None:
    examples = _normative()
    documents = _normative_documents()
    assert len(examples) == len(documents)
    for example, (raw, whole) in zip(examples, documents, strict=True):
        source = generate(load(raw))
        if whole:
            assert "\n".join(blocks(source)) + "\n" == example
        else:
            assert example in source


# Every kind in one scene


def every_kind_2d() -> Raw:
    """One 2D document that uses every kind a 2D scene offers."""
    objects = [
        labelled_axes(),
        plot(),
        point(),
        title(),
        formula("eq_1", "x"),
        formula("eq_2", "y"),
        shape("dot"),
        shape("line"),
        shape("arrow"),
        shape("circle"),
        shape("rectangle", fillOpacity=0.5),
    ]
    beats = [
        beat("create", "axes_1", "plot_1"),
        beat("grow", "point_1"),
        beat("write", "title_1", "eq_1"),
        beat("fade_in", "dot_1", "line_1"),
        beat("grow_arrow", "arrow_1"),
        beat("draw_border_then_fill", "rectangle_1"),
        beat("spin_in", "circle_1"),
        beat("wait"),
        beat("move", "title_1", params={"shift": [0, 1]}),
        beat("move_to", "dot_1", "line_1", params={"point": [1, 1]}),
        beat("scale", "circle_1", "rectangle_1", params={"factor": 0.5}, easing="there_and_back"),
        beat("rotate", "arrow_1", params={"angle": 45}, duration=0.5),
        beat("set_opacity", "plot_1", "circle_1", params={"opacity": 0.5}),
        beat("set_color", "axes_1", "title_1", params={"color": "#FF0000"}),
        beat("move_along", "point_1", "plot_1", easing="rush_from"),
        beat("indicate", "axes_1", "plot_1", "point_1", "title_1"),
        beat("circumscribe", "eq_1"),
        beat("flash", "dot_1"),
        beat("wiggle", "axes_1", "plot_1", "dot_1"),
        beat("focus", "circle_1"),
        beat("wave", "title_1"),
        beat("transform_formula", "eq_1", "eq_2"),
        beat("unwrite", "title_1", easing="rush_into"),
        beat("transform", "circle_1", "title_1"),
        beat("uncreate", "line_1"),
        beat("shrink", "dot_1"),
        beat("fade_out", "arrow_1", "rectangle_1"),
        beat("fade_in", "circle_1"),
    ]
    return document(objects, beats)


def every_kind_3d() -> Raw:
    """One 3D document that uses every kind a 3D scene offers."""
    return three_d_with(
        beat("move_camera", params={"phi": 75, "theta": 30, "zoom": 0.9}),
        beat("indicate", "axes_1", "curve_1"),
        beat("wiggle", "curve_1"),
        beat("move", "curve_1", params={"shift": [1, 0]}),
        beat("move_to", "axes_1", "curve_1", params={"point": [0, 0]}),
        beat("scale", "curve_1", params={"factor": 1.2}),
        beat("rotate", "axes_1", "curve_1", params={"angle": 30}),
        beat("set_opacity", "curve_1", params={"opacity": 0.5}),
        beat("set_color", "curve_1", params={"color": "#E07A5F"}),
        beat("uncreate", "curve_1"),
        beat("fade_in", "curve_1"),
        beat("fade_out", "curve_1", "axes_1"),
        beat("grow", "axes_1"),
        beat("create", "curve_1"),
        beat("shrink", "curve_1"),
        beat("wait"),
        beat("move_camera", params={"phi": 60, "theta": -30, "zoom": 1}, easing="there_and_back"),
    )


EVERY_KIND: dict[str, Callable[[], Raw]] = {"2d": every_kind_2d, "3d": every_kind_3d}


def test_the_two_documents_use_every_kind() -> None:
    used = {item["action"] for build in EVERY_KIND.values() for item in build()["scene"]["beats"]}
    assert used == set(KINDS)


@pytest.mark.parametrize("name", EVERY_KIND)
def test_a_scene_of_every_kind_compiles(name: str, tmp_path: Path) -> None:
    scene = tmp_path / "scene.py"
    scene.write_text(generate(load(EVERY_KIND[name]())), encoding="utf-8")
    py_compile.compile(str(scene), cfile=str(tmp_path / "scene.pyc"), doraise=True)


@pytest.mark.parametrize("name", EVERY_KIND)
def test_each_beat_is_one_play_or_wait(name: str) -> None:
    raw = EVERY_KIND[name]()
    construct = _construct(ast.parse(generate(load(raw))))
    calls = [
        node.func.attr
        for node in ast.walk(construct)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr in {"play", "wait", "move_camera"}
    ]
    assert len(calls) == len(raw["scene"]["beats"])


@pytest.mark.parametrize("name", EVERY_KIND)
def test_the_scene_names_only_its_own_variables_and_manims(name: str) -> None:
    module = ast.parse(generate(load(EVERY_KIND[name]())))
    bound = {node.id for node in ast.walk(module) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    bound |= {node.arg for node in ast.walk(module) if isinstance(node, ast.arg)}
    bound |= {node.name for node in ast.walk(module) if isinstance(node, ast.FunctionDef | ast.ClassDef)}
    loaded = {node.id for node in ast.walk(module) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    # ``np`` is imported; ``round`` and ``max`` are the builtins of ``_beat_time``.
    assert loaded - bound <= MANIM_NAMES | set(EASINGS) | {"np", "round", "max"}


def test_the_manim_names_are_manims() -> None:
    """Every name the scene takes from Manim is one that ``from manim import *`` binds."""
    manim = pytest.importorskip("manim")
    # Without an ``__all__``, the star import binds every public name of the package.
    exported = getattr(manim, "__all__", None) or [name for name in vars(manim) if not name.startswith("_")]
    assert MANIM_NAMES | set(EASINGS) <= set(exported)


def _construct(module: ast.Module) -> ast.FunctionDef:
    return next(node for node in ast.walk(module) if isinstance(node, ast.FunctionDef) and node.name == "construct")


# Easing, kind by kind


def _play_line(raw: Raw) -> str:
    """The play line of the last beat."""
    lines = generate(load(raw)).splitlines()
    return next(line for line in reversed(lines) if "self.play(" in line or "self.move_camera(" in line)


@pytest.mark.parametrize("action", [action for action in KINDS if action != "wait"])
def test_an_easing_is_written_only_when_named_or_unlike_manims_own(action: str) -> None:
    kind = KINDS[action]
    default = _play_line(CASES[action])
    if kind.default_easing == kind.manim_default:
        assert "rate_func" not in default
    else:
        assert default.endswith(f"run_time=1, rate_func={kind.default_easing})")
    for name in kind.easings:
        raw = json.loads(json.dumps(CASES[action]))
        raw["scene"]["beats"][-1]["easing"] = name
        written = MIRROR[name] if kind.reversed_rate else name
        assert _play_line(raw).endswith(f"run_time=1, rate_func={written})"), name


# What a beat's cache key holds


def _key_changes(raw: Raw, edit: Callable[[Raw], None]) -> list[bool]:
    """For each beat, whether ``edit`` changes its preview key."""
    edited = json.loads(json.dumps(raw))
    edit(edited)
    before, after = beat_cache_keys(load(raw), "preview"), beat_cache_keys(load(edited), "preview")
    return [old != new for old, new in zip(before, after, strict=True)]


def _set_beat(index: int, **fields: object) -> Callable[[Raw], None]:
    def edit(raw: Raw) -> None:
        raw["scene"]["beats"][index].update(fields)

    return edit


def _set_param(object_id: str, key: str, value: object) -> Callable[[Raw], None]:
    def edit(raw: Raw) -> None:
        item = next(item for item in raw["scene"]["objects"] if item["id"] == object_id)
        item["params"][key] = value

    return edit


@pytest.mark.parametrize(
    ("edit", "changed"),
    [
        (_set_beat(1, easing="linear"), [False, True]),
        (_set_beat(1, params={"factor": 3}), [False, True]),
        (_set_beat(1, action="rotate", params={"angle": 30}), [False, True]),
        (_set_beat(1, duration=2), [False, True]),
        (_set_param("circle_1", "fillOpacity", 0.5), [True, True]),
    ],
    ids=["easing", "setting", "kind", "duration", "object"],
)
def test_a_beat_key_holds_its_fields_and_its_objects(edit: Callable[[Raw], None], changed: list[bool]) -> None:
    assert _key_changes(CASES["scale"], edit) == changed


@pytest.mark.parametrize(
    ("case", "edit", "changed"),
    [
        ("move_along", _set_param("circle_1", "radius", 2), [True, True]),
        ("move_along", _set_param("dot_1", "radius", 0.2), [True, True]),
        ("transform", _set_param("rectangle_1", "width", 3), [False, True]),
        ("transform", _set_param("circle_1", "radius", 2), [True, True]),
        ("a transform into a plot on its axes", _set_param("axes_1", "xLength", 6), [True, True]),
    ],
    ids=["path", "mover", "destination", "source", "axes of a destination"],
)
def test_a_pair_holds_both_its_objects(case: str, edit: Callable[[Raw], None], changed: list[bool]) -> None:
    assert _key_changes(CASES[case], edit) == changed


@pytest.mark.parametrize(
    ("case", "edit", "changed"),
    [
        ("an entrance of several objects", _set_param("circle_1", "radius", 2), [True]),
        (
            "opacity on every 2D type",
            _set_param("rectangle_1", "fillOpacity", 0.6),
            [False, False, False, True, False, True],
        ),
    ],
    ids=["entrance", "change"],
)
def test_a_beat_holds_every_target_not_only_the_first(
    case: str, edit: Callable[[Raw], None], changed: list[bool]
) -> None:
    assert _key_changes(CASES[case], edit) == changed


def test_a_point_on_axes_holds_its_axes() -> None:
    raw = CASES["a point on axes at its fields"]
    assert _key_changes(raw, _set_param("axes_1", "yLength", 3)) == [True, True]


def test_a_camera_move_holds_its_settings_and_no_object() -> None:
    raw = CASES["move_camera"]
    assert _key_changes(raw, _set_beat(2, params={"phi": 70, "theta": 45.5, "zoom": 0.8})) == [False, False, True]
    assert _key_changes(raw, _set_beat(2, easing="linear")) == [False, False, True]

    def recolour(edited: Raw) -> None:
        edited["scene"]["templateInstances"][0]["params"]["curveColor"] = "#FFFFFF"

    assert _key_changes(raw, recolour) == [False, True, False]
