"""The projects the quick render smoke tests open: every beat kind, shape and easing, rendered for real.

Each project is ``tests/fixtures/render-smoke/<name>/project.json``, built
here and kept current by ``tests/test_smoke_projects.py`` (``uv run pytest
tests/test_smoke_projects.py --update-golden`` rewrites them). Beats last half
a second, so the preview profile (854×480 at 15 fps) draws each one quickly.

- ``kind-<action>``: one scene per beat kind, with the fewest objects it needs
  (shapes and text where they do; formulas only for ``transform_formula``).
- ``arrow``: every kind that takes an arrow, on one arrow.
- ``shape-<type>``: each shape on the entrance Add gives it, and
  ``shapes-changed``: every shape moved, turned, scaled and recoloured.
- ``opacity``: opacity 0.5, then 1, on one object of every 2D type.
- ``several-targets``: an entrance that lists a plot before its axes, the
  changes on two and on three overlapping objects, indicate and wiggle on
  labelled axes with what is drawn on them and a text, and exits of two.
- ``easings``: one scale per easing; ``three-d-curve`` has one camera move per easing.
- ``re-entry``: objects that leave and come back by each entrance and exit,
  a plot drawn again on axes that moved, and a transform's source entering again.
- ``three-d-curve``, ``three-d-surface``: every kind a 3D scene offers, on the
  curve, and the changes and re-entry of an 8×8 surface.
- ``stable-cache``: a move along a path, a wiggle and a wave, then three
  plays while both objects stay on screen.

A render of each of the ``CACHE_SCENES`` (``re-entry``, ``several-targets``,
``stable-cache`` and the two 3D scenes), repeated with no edit, shows whether
Manim reuses every clip. Between them they play every kind of
``UNSTABLE_KINDS`` with another play after it.
"""

from __future__ import annotations

import json
from typing import Any

from mathimator_worker.beats import EASINGS, KINDS, NATURAL_ENTRANCE, SHAPES
from mathimator_worker.document import dumps_project, load_text
from tests.beat_documents import Raw, axes, beat, document, formula, plot, shape, text, three_d
from tests.support import REPO

FOLDER = REPO / "tests" / "fixtures" / "render-smoke"
SECONDS = 0.5
CACHE_SCENES = ["re-entry", "several-targets", "stable-cache", "three-d-curve", "three-d-surface"]
# Kinds whose Manim key changed with the string-hash seed (rotate, wave) or whose end state depends on the frames
# drawn (move along a path, wiggle, a camera move), until the worker made them stable (sections 5 and 10).
UNSTABLE_KINDS = {"rotate", "wave", "move_along", "wiggle", "move_camera"}
# Where each camera move of the 3D scenes turns to.
CAMERA_STOPS = [
    {"phi": 75, "theta": -15, "zoom": 0.9},
    {"phi": 60, "theta": 30, "zoom": 1.1},
    {"phi": 45, "theta": 60, "zoom": 1},
]


def step(action: str, *targets: str, params: Raw | None = None, easing: str | None = None) -> Raw:
    """A beat of half a second."""
    return beat(action, *targets, params=params, easing=easing, duration=SECONDS)


def defaults(action: str) -> Raw | None:
    """The settings a new beat of ``action`` gets, as the catalog gives them."""
    params = KINDS[action].params
    return {param.name: param.default for param in params} if params else None


def scene(objects: list[Raw], beats: list[Raw]) -> Raw:
    return document(objects, beats)


def three_d_scene(beats: list[Raw], mode: str) -> Raw:
    """The 3D template, its two beats cut to half a second, then ``beats``."""
    raw = three_d(beats, mode)
    for item in raw["scene"]["beats"][:2]:
        item["duration"] = SECONDS
    return raw


def labelled_axes() -> Raw:
    item = axes()
    item["params"]["showLabels"] = True
    return item


def placed(item: Raw, anchor: str) -> Raw:
    item["params"]["anchor"] = anchor
    return item


def point(**params: Any) -> Raw:
    return shape("axes_point", "point_1", **params)


def on(action: str, item: Raw) -> Raw:
    """``item`` enters on its natural entrance, then one beat of ``action``, on its default settings, acts on it."""
    object_id = item["id"]
    return scene(
        [item], [step(NATURAL_ENTRANCE[item["type"]], object_id), step(action, object_id, params=defaults(action))]
    )


def kind_project(action: str) -> Raw:
    """The fewest objects ``action`` needs, and a beat of it."""
    family = KINDS[action].family
    circle = shape("circle")
    if action == "wait":
        return scene([circle], [step("fade_in", "circle_1"), step("wait")])
    if action == "move_camera":
        return three_d_scene([step("move_camera", params=CAMERA_STOPS[0])], "surface")
    if action == "move_along":
        return scene(
            [circle, shape("dot")], [step("fade_in", "circle_1", "dot_1"), step("move_along", "dot_1", "circle_1")]
        )
    if action == "transform":
        return scene(
            [circle, shape("rectangle")], [step("create", "circle_1"), step("transform", "circle_1", "rectangle_1")]
        )
    if action == "transform_formula":
        return scene(
            [formula("eq_1", r"y = \sin(x)"), formula("eq_2", r"y = \cos(x)")],
            [step("write", "eq_1"), step("transform_formula", "eq_1", "eq_2")],
        )
    item = {
        "write": text(),
        "unwrite": text(),
        "grow_arrow": shape("arrow"),
        "draw_border_then_fill": shape("circle", fillOpacity=0.5),
        "spin_in": shape("rectangle"),
        "rotate": shape("rectangle"),
    }.get(action, circle)
    if family == "entrance":
        return scene([item], [step(action, item["id"])])
    return on(action, item)


def arrow_project() -> Raw:
    """Every kind that takes an arrow, on one arrow; a dot moves along it, and it morphs into a circle and back."""
    changes = ["move", "move_to", "scale", "rotate", "set_opacity", "set_color"]
    emphasis = ["indicate", "circumscribe", "flash", "wiggle", "focus", "wave"]
    beats = [
        step("grow_arrow", "arrow_1"),
        *(step(action, "arrow_1", params=defaults(action)) for action in [*changes, *emphasis]),
        step("fade_in", "dot_1"),
        step("move_along", "dot_1", "arrow_1"),
        step("uncreate", "arrow_1"),
        step("create", "arrow_1"),
        step("fade_out", "arrow_1"),
        step("fade_in", "arrow_1"),
        step("transform", "arrow_1", "circle_1"),
        step("transform", "circle_1", "arrow_1"),
    ]
    return scene([shape("arrow"), shape("dot", center=[2, -2]), shape("circle")], beats)


def shape_project(shape_type: str) -> Raw:
    """A shape on the entrance Add gives it; a point on axes after its axes."""
    item = shape(shape_type)
    entrance = step(SHAPES[shape_type].entrance, item["id"])
    if shape_type == "axes_point":
        return scene([axes(), item], [step("create", "axes_1"), entrance])
    return scene([item], [entrance])


def shapes_changed_project() -> Raw:
    """Every shape moved, turned, scaled and recoloured, all six in each beat."""
    objects = [
        axes(),
        shape("dot", center=[-3, 1]),
        shape("line", start=[-3, -2], end=[-1, -1]),
        shape("arrow", start=[1, -2], end=[3, -1]),
        shape("circle", center=[2, 1], radius=0.75),
        shape("rectangle", width=2, height=1, fillOpacity=0.3),
        point(x=-1, y=1),
    ]
    ids = ["dot_1", "line_1", "arrow_1", "circle_1", "rectangle_1", "point_1"]
    beats = [
        step("create", "axes_1"),
        step("grow", "dot_1", "point_1"),
        step("create", "line_1", "circle_1", "rectangle_1"),
        step("grow_arrow", "arrow_1"),
        step("move", *ids, params={"shift": [0.5, 0.5]}),
        step("rotate", *ids, params={"angle": 30}),
        step("scale", *ids, params={"factor": 0.8}),
        step("set_color", *ids, params={"color": "#F2CC8F"}),
    ]
    return scene(objects, beats)


def opacity_project() -> Raw:
    """Opacity 0.5, then 1, on one object of every 2D type: an outline circle, a part-filled rectangle, labelled axes."""
    objects = [
        labelled_axes(),
        plot(),
        point(),
        placed(text(), "edge_up"),
        placed(formula(), "edge_down"),
        shape("dot", center=[-3, -1]),
        shape("line", start=[-3, 1], end=[-1, 2]),
        shape("arrow", start=[1, 1], end=[3, 2]),
        shape("circle", center=[2, -1], radius=0.75),
        shape("rectangle", width=2, height=1, fillOpacity=0.3),
    ]
    ids = [item["id"] for item in objects]
    beats = [
        step("create", "axes_1", "plot_1"),
        step("grow", "point_1", "dot_1"),
        step("write", "text_1", "eq_1"),
        step("create", "line_1", "circle_1", "rectangle_1"),
        step("grow_arrow", "arrow_1"),
        step("set_opacity", *ids, params={"opacity": 0.5}),
        step("set_opacity", *ids, params={"opacity": 1}),
    ]
    return scene(objects, beats)


def several_targets_project() -> Raw:
    """Labelled axes, a plot, a point, a text and a filled rectangle that overlap, acting two and three at a time."""
    objects = [
        labelled_axes(),
        plot(),
        point(),
        text(),
        shape("rectangle", width=3, height=1.5, fillOpacity=0.6),
    ]
    two = ("text_1", "rectangle_1")
    three = ("axes_1", "plot_1", "point_1")
    changes = [
        ("move", {"shift": [0.5, 0]}, {"shift": [0, -0.5]}),
        ("move_to", {"point": [1, 1]}, {"point": [0, 0]}),
        ("scale", {"factor": 1.2}, {"factor": 0.8}),
        ("rotate", {"angle": 30}, {"angle": -30}),
        ("set_opacity", {"opacity": 0.6}, {"opacity": 0.6}),
        ("set_color", {"color": "#F2CC8F"}, {"color": "#81B29A"}),
    ]
    beats = [
        step("create", "plot_1", "axes_1"),
        step("grow", "point_1"),
        step("write", "text_1"),
        step("create", "rectangle_1"),
    ]
    for action, on_two, on_three in changes:
        beats += [step(action, *two, params=on_two), step(action, *three, params=on_three)]
    beats += [
        step("indicate", *three, "text_1"),
        step("wiggle", *three, "text_1"),
        step("fade_out", *two),
        step("uncreate", "plot_1", "point_1"),
    ]
    return scene(objects, beats)


def easings_project() -> Raw:
    """One scale per easing, doubling and halving in turn."""
    beats = [step("fade_in", "circle_1")]
    for index, name in enumerate(EASINGS):
        beats.append(step("scale", "circle_1", params={"factor": 0.5 if index % 2 else 2}, easing=name))
    return scene([shape("circle", radius=0.5)], beats)


def re_entry_project() -> Raw:
    """Each entrance and exit on the same objects, a plot drawn again on moved axes, and a transform's source back."""
    objects = [
        axes(),
        plot(),
        shape("circle"),
        placed(text(), "edge_up"),
        shape("rectangle", fillOpacity=0.3),
    ]
    beats = [
        step("fade_in", "circle_1"),
        step("fade_out", "circle_1"),
        step("create", "circle_1"),
        step("uncreate", "circle_1"),
        step("grow", "circle_1"),
        step("shrink", "circle_1"),
        step("spin_in", "circle_1"),
        step("fade_out", "circle_1"),
        step("draw_border_then_fill", "circle_1"),
        step("write", "text_1"),
        step("unwrite", "text_1"),
        step("write", "text_1"),
        step("create", "axes_1", "plot_1"),
        step("fade_out", "plot_1"),
        step("move", "axes_1", params={"shift": [0, -1]}),
        step("create", "plot_1"),
        step("transform", "circle_1", "rectangle_1"),
        step("create", "circle_1"),
    ]
    return scene(objects, beats)


def three_d_curve_project() -> Raw:
    """Every kind a 3D scene offers, on the curve and its axes, then one camera move per easing."""
    params = {action: defaults(action) for action in KINDS}
    beats = [
        step("indicate", "curve_1"),
        step("indicate", "axes_1", "curve_1"),
        step("wiggle", "curve_1"),
        step("wiggle", "axes_1", "curve_1"),
        *(step(action, "curve_1", params=params[action]) for action in ("move", "move_to", "scale", "rotate")),
        step("set_opacity", "curve_1", params={"opacity": 0.5}),
        step("set_color", "curve_1", params=params["set_color"]),
        step("fade_out", "curve_1"),
        step("fade_in", "curve_1"),
        step("uncreate", "curve_1"),
        step("grow", "curve_1"),
        step("shrink", "curve_1"),
        step("create", "curve_1"),
        step("fade_out", "curve_1", "axes_1"),
        step("grow", "axes_1"),
        step("create", "curve_1"),
        step("wait"),
    ]
    beats += [
        step("move_camera", params=CAMERA_STOPS[index % len(CAMERA_STOPS)], easing=name)
        for index, name in enumerate(EASINGS)
    ]
    return three_d_scene(beats, "curve")


def three_d_surface_project() -> Raw:
    """The template's 8×8 surface changed, emphasised, shrunk and grown, then entering again."""
    beats = [
        step("indicate", "surface_1"),
        step("wiggle", "surface_1"),
        step("rotate", "surface_1", params={"angle": 45}),
        step("scale", "surface_1", params={"factor": 0.8}),
        step("move_to", "surface_1", params={"point": [0.5, 0]}),
        step("set_opacity", "surface_1", params={"opacity": 0.5}),
        step("shrink", "surface_1"),
        step("grow", "surface_1"),
        step("uncreate", "surface_1"),
        step("create", "surface_1"),
    ]
    return three_d_scene(beats, "surface")


def stable_cache_project() -> Raw:
    """A move along a path, a wiggle and a wave, then three plays while both objects stay on screen."""
    beats = [
        step("fade_in", "circle_1", "dot_1"),
        step("move_along", "dot_1", "circle_1"),
        step("wiggle", "circle_1", "dot_1"),
        step("wave", "circle_1"),
        step("set_color", "circle_1", params={"color": "#F2CC8F"}),
        step("rotate", "circle_1", params={"angle": 90}),
        step("indicate", "dot_1"),
    ]
    return scene([shape("circle"), shape("dot")], beats)


def projects() -> dict[str, Raw]:
    """Every smoke project by folder name, each named after its folder."""
    built = {f"kind-{action}": kind_project(action) for action in KINDS}
    built["arrow"] = arrow_project()
    built |= {f"shape-{shape_type}": shape_project(shape_type) for shape_type in SHAPES}
    built |= {
        "shapes-changed": shapes_changed_project(),
        "opacity": opacity_project(),
        "several-targets": several_targets_project(),
        "easings": easings_project(),
        "re-entry": re_entry_project(),
        "three-d-curve": three_d_curve_project(),
        "three-d-surface": three_d_surface_project(),
        "stable-cache": stable_cache_project(),
    }
    for name, raw in built.items():
        raw["id"] = name.replace("-", "_")
        raw["name"] = f"Smoke: {name}"
    return built


def project_text(raw: Raw) -> str:
    """``raw`` as Mathimator saves it."""
    return dumps_project(load_text(json.dumps(raw)))
