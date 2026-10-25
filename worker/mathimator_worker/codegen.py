"""Deterministic Manim scene source for generatorVersion 2.

The Journey A block and ``reference-60`` are whitespace-exact. A blank line
separates beat blocks until the first non-wait beat that follows a wait;
the beats after that stay in one block. That is the pattern of the normative
scenes. Each beat is still exactly one ``play`` or ``wait``.

A beat block builds the objects the beat brings on screen, then holds the
lines that prepare its play, the play, and the lines that settle what the play
left; only the play draws frames. Every entrance builds its objects afresh,
each from its fields, so an object that leaves and comes back looks as its
fields define it, whatever earlier beats did to it. A beat on several objects
plays one animation per object, never a group: Manim would draw a played group
above everything else from then on. The calls come from the catalog rows in
``beats.py``. An easing is the play's ``rate_func``, written only when the beat
names one or its kind's default is not the animation's own, so a document
written before easings compiles to the code it always did.

Every beat covers the frames between its start and its end on the timeline,
each rounded to the nearest frame from whole milliseconds, halves up, as the
editor's playback rounds them (D63). A whole second is a whole number of
frames at every frame rate, so a beat between whole seconds keeps its length
as a literal. Any other beat calls ``_beat_time``, which the scene defines only
then. It reads the frame rate when the scene renders, because one ``scene.py``
renders the preview and every export.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from mathimator_worker.beats import (
    AXES_CHILD_TYPES,
    EASINGS,
    KINDS,
    MIRROR,
    SHAPE_TYPES,
    BeatKind,
    effective_easing,
    states,
)
from mathimator_worker.document import (
    CURVE_VARIABLES,
    MANIM_VERSION,
    PLOT_VARIABLES,
    SURFACE_VARIABLES,
    Beat,
    Project,
    SceneObject,
    derived_starts,
    quantize,
    total_duration,
)
from mathimator_worker.document_error import DocumentError
from mathimator_worker.expr import expression_to_python
from mathimator_worker.jsonio import JsonValue
from mathimator_worker.timeline import canonical_targets

_SCENE_CLASS = {"scene": "Scene", "three_d_scene": "ThreeDScene"}
_ANCHOR_CALL = {
    "origin": "move_to(ORIGIN)",
    "edge_up": "to_edge(UP)",
    "edge_down": "to_edge(DOWN)",
    "edge_left": "to_edge(LEFT)",
    "edge_right": "to_edge(RIGHT)",
}
# Manim's set_opacity sets the fill as well as the stroke, which would fill a
# curve or a line between its ends and fill an outline shape. These fade their
# stroke instead, and a circle or a rectangle keeps its fill in proportion.
_STROKE_ONLY = frozenset({"function_plot", "parametric_curve", "line"})
_OWN_FILL = frozenset({"circle", "rectangle"})


# Manim draws ceil(run_time * rate) frames for a play and floor(seconds * rate)
# for a static wait, so half a frame of slack keeps either count exact. A play of
# one frame takes 1 / rate, the shortest run_time Manim accepts without a warning.
_BEAT_TIME = [
    "def _beat_time(start, end, wait=False):",
    "    # Seconds that make Manim draw the frames from start to end, each rounded",
    "    # to the nearest frame, halves up. Times are whole milliseconds, so this is",
    "    # integer math: in floats, 4.1 * 15 is 61.4999... and would round down.",
    "    # A play rounds up and a wait down: hence the half frame.",
    "    rate = config.frame_rate",
    "    frames = (round(end * 1000) * rate + 500) // 1000 - (round(start * 1000) * rate + 500) // 1000",
    "    if wait:",
    "        return (frames + 0.5) / rate",
    "    return max(frames - 0.5, 1) / rate",
    "",
    "",
]


class CodegenError(Exception):
    """The document cannot be compiled in this phase."""


@dataclass(frozen=True)
class _Play:
    """The animations of a beat's one play, with the lines before and after it."""

    animations: list[str]
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)


def generate(project: Project) -> str:
    """Return ``scene.py`` bytes without writing them."""
    scene_class = _SCENE_CLASS.get(project.scene.kind)
    if scene_class is None:
        raise CodegenError(f"unknown scene kind {project.scene.kind!r}")
    objects = {item.id: item for item in project.scene.objects}
    beats = project.scene.beats
    bounds = [*derived_starts([beat.duration for beat in beats]), total_duration(project)]
    blocks = [
        _emit_beat(project, objects, beat, start, end)
        for beat, (start, end) in zip(beats, pairwise(bounds), strict=True)
    ]
    header = _header(project, scene_class, beat_time=not all(bound.is_integer() for bound in bounds))
    if not blocks:
        return header + "\n"
    return header + "\n\n" + _join_blocks(blocks) + "\n"


def needs_latex(project: Project) -> bool:
    """Whether the scene runs LaTeX: it has a formula, or axes with their labels (D31)."""
    return any(
        item.type == "math_formula" or (item.type == "axes" and item.params.get("showLabels") is True)
        for item in project.scene.objects
    )


def _header(project: Project, scene_class: str, *, beat_time: bool) -> str:
    """The module top. ``beat_time`` adds the frame helper, which only a beat off whole seconds calls."""
    lines = [
        f"# Generated by Mathimator. generatorVersion {project.generator_version}. manim=={MANIM_VERSION}",
        "from manim import *",
        "import numpy as np",
        "",
        f"config.seed = {project.seed}",
        "",
        "",
        *(_BEAT_TIME if beat_time else []),
        f"class MainScene({scene_class}):",
        "    def construct(self):",
        f'        self.camera.background_color = "{project.scene.background}"',
    ]
    if project.scene.kind == "three_d_scene":
        camera = project.scene.camera
        if camera is None:
            raise CodegenError("a 3D scene needs a camera")
        lines.append(
            "        self.set_camera_orientation("
            f"phi={_number(camera.phi)} * DEGREES, "
            f"theta={_number(camera.theta)} * DEGREES, "
            f"zoom={_number(camera.zoom)})"
        )
    return "\n".join(lines)


def _join_blocks(blocks: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    pack = False
    for index, (action, text) in enumerate(blocks):
        if index == 0:
            parts.append(text)
            continue
        separator = "\n" if pack else "\n\n"
        parts.append(separator + text)
        previous = blocks[index - 1][0]
        if not pack and previous == "wait" and action != "wait":
            pack = True
    return "".join(parts)


def _emit_beat(
    project: Project, objects: Mapping[str, SceneObject], beat: Beat, start: float, end: float
) -> tuple[str, str]:
    """The block of one beat: the objects it brings on screen, the lines before its play, the play, the lines after."""
    kind = KINDS.get(beat.action)
    if kind is None:
        raise CodegenError(f"unknown action {beat.action}")
    if kind.family == "hold":
        return beat.action, f"        self.wait({_length(start, end, wait=True)})"
    timing = _length(start, end) + _rate_func(kind, beat.easing)
    if kind.family == "camera":
        return beat.action, _camera_move(beat, timing)
    targets = [objects[target] for target in beat.targets]
    entering = [
        target
        for target, (before, after) in zip(beat.targets, states(kind, len(targets)), strict=True)
        if (before, after) == ("off", "on")
    ]
    # Axes are built before what is drawn on them, whatever order the targets are stored in.
    lines = [line for target in canonical_targets(project, beat.action, entering) for line in _built(objects[target])]
    play = _pair_play(kind, beat, targets) if kind.arity == "pair" else _play_each(kind, beat, targets)
    lines.extend(play.before)
    lines.append(f"        self.play({', '.join(play.animations)}, run_time={timing})")
    lines.extend(play.after)
    return beat.action, "\n".join(lines)


def _play_each(kind: BeatKind, beat: Beat, targets: Sequence[SceneObject]) -> _Play:
    """One animation per target, in target order, each about its centre when it has one."""
    variables = [_variable(kind, item) for item in targets]
    before, centres = _centres(kind, targets, variables)
    settings = _settings(kind, beat)
    animations: list[str] = []
    for item, variable in zip(targets, variables, strict=True):
        centre = centres.get(item.id)
        if centre is not None:
            animations.append(_filled(kind.centred_call, kind, v=variable, c=centre, **settings))
        elif kind.action == "set_opacity" and item.type in _STROKE_ONLY | _OWN_FILL:
            animations.append(_faded(item, variable, _params(beat)["opacity"]))
        else:
            animations.append(_filled(kind.call, kind, v=variable, **settings))
    if not kind.restore:
        return _Play(animations, before)
    # Where these animations leave an object depends on the frames drawn, and Manim's key for a
    # later play holds everything on screen, so each target is put back exactly as it started.
    before.extend(f"        {variable}.save_state()" for variable in variables)
    return _Play(animations, before, [f"        {variable}.restore()" for variable in variables])


def _pair_play(kind: BeatKind, beat: Beat, targets: Sequence[SceneObject]) -> _Play:
    """The one animation of a pair, each role name in its call standing for that target's variable."""
    roles = {role.name: _variable(kind, item) for role, item in zip(kind.roles, targets, strict=True)}
    animation = _filled(kind.call, kind, **roles)
    if kind.action == "transform":
        # A copy morphs into the destination, so the source itself leaves unchanged.
        return _Play([animation], [f"        self.remove({roles['source']})"])
    if kind.action == "move_along":
        # Where the mover ends depends on the frames drawn, so it is put back and moved to
        # the exact end of the path: its start, for an easing that comes back.
        mover = roles["mover"]
        end = f"{roles['path']}.point_from_proportion({_ends_at(kind, beat.easing)})"
        return _Play(
            [animation],
            [f"        {mover}.save_state()"],
            [f"        {mover}.restore()", f"        {mover}.move_to({end})"],
        )
    return _Play([animation])


def _centres(
    kind: BeatKind, targets: Sequence[SceneObject], variables: Sequence[str]
) -> tuple[list[str], dict[str, str]]:
    """The lines that measure the centres targets act about, and the centre of each target that has one.

    Changes about a centre act about the centre of all their targets, so axes and
    what is drawn on them stay together; the group is only measured, never
    played. Indicate and wiggle act about the centre of axes that are targets
    together with something drawn on them, and every other target about its own.
    """
    if kind.centre == "shared" and len(targets) > 1:
        line = f"        _center = VGroup({', '.join(variables)}).get_center()"
        return [line], dict.fromkeys((item.id for item in targets), "_center")
    if kind.centre != "axes":
        return [], {}
    lines: list[str] = []
    centres: dict[str, str] = {}
    for axes, variable in zip(targets, variables, strict=True):
        drawn = [item.id for item in targets if item.type in AXES_CHILD_TYPES and item.params.get("axesId") == axes.id]
        if drawn:
            name = f"_center_{axes.id}"
            lines.append(f"        {name} = {variable}.get_center()")
            centres.update(dict.fromkeys([axes.id, *drawn], name))
    return lines, centres


def _faded(item: SceneObject, variable: str, opacity: JsonValue) -> str:
    """The set_opacity of a curve, a line or an outline shape: its stroke fades, and its own fill in proportion."""
    stroke = f"{variable}.animate.set_stroke(opacity={_number(opacity)})"
    if item.type in _STROKE_ONLY:
        return stroke
    return f"{stroke}.set_fill(opacity={_number(_float(opacity) * _float(item.params['fillOpacity']))})"


def _camera_move(beat: Beat, timing: str) -> str:
    """``move_camera`` turns the camera's trackers in one ``self.play``.

    The camera keeps the rotation of the last frame it drew, and Manim's key for
    the next play holds it, so a move Manim drew and one it reused from its
    cache, which draws no frame, would leave different keys. The rotation is
    reset to where the move ends, which changes no frame: the camera resets it
    before each frame it draws.
    """
    params = _params(beat)
    return (
        f"        self.move_camera(phi={_number(params['phi'])} * DEGREES, "
        f"theta={_number(params['theta'])} * DEGREES, zoom={_number(params['zoom'])}, run_time={timing})\n"
        "        self.camera.reset_rotation_matrix()"
    )


def _rate_func(kind: BeatKind, easing: str | None) -> str:
    """``, rate_func=<name>`` when the beat names an easing or its kind's default is not the animation's own.

    Uncreate and unwrite run their rate function backwards, so they get the
    mirror image, which draws what the easing's label says.
    """
    name = effective_easing(kind, easing)
    if name is None or (easing is None and name == kind.manim_default):
        return ""
    return f", rate_func={MIRROR[name] if kind.reversed_rate else name}"


def _ends_at(kind: BeatKind, easing: str | None) -> int:
    """Where the beat's easing leaves the animation: 1 at its end, 0 back at its start."""
    name = effective_easing(kind, easing)
    if name is None:
        raise CodegenError(f"{kind.action} needs an easing")
    return EASINGS[name].ends_at


def _filled(template: str | None, kind: BeatKind, **fields: str) -> str:
    if template is None:
        raise CodegenError(f"{kind.action} has no code")
    return template.format(**fields)


def _settings(kind: BeatKind, beat: Beat) -> dict[str, str]:
    """The text of each setting, as its row says: a number at three decimals (D02), a vector as ``<name>_x``, ``_y``."""
    if not kind.params:
        return {}
    params = _params(beat)
    texts: dict[str, str] = {}
    for param in kind.params:
        value = params[param.name]
        if param.type == "vector":
            if not isinstance(value, list) or len(value) != 2:
                raise CodegenError(f"{beat.action} {param.name} must be two numbers")
            texts[f"{param.name}_x"] = _shift_component(value[0])
            texts[f"{param.name}_y"] = _shift_component(value[1])
        elif param.type == "number":
            texts[param.name] = _number(value)
        elif isinstance(value, str):
            texts[param.name] = value
        else:
            raise CodegenError(f"{beat.action} {param.name} must be text")
    return texts


def _params(beat: Beat) -> dict[str, JsonValue]:
    """The params of a beat whose kind has settings, which load always gives it."""
    if beat.params is None:
        raise CodegenError(f"{beat.action} needs params")
    return beat.params


def _length(start: float, end: float, *, wait: bool = False) -> str:
    """The emitted length of a beat from ``start`` to ``end`` on the timeline (D63)."""
    if start.is_integer() and end.is_integer():
        return _number(end - start)
    arguments = f"{_number(start)}, {_number(end)}"
    return f"_beat_time({arguments}, wait=True)" if wait else f"_beat_time({arguments})"


def _variable(kind: BeatKind, target: SceneObject) -> str:
    """The variable a beat animates. Labels belong to their axes and act with them (D12), except under a bare kind."""
    if not kind.bare and target.type == "axes" and target.params.get("showLabels") is True:
        return f"{target.id}_group"
    return target.id


def _built(target: SceneObject) -> list[str]:
    """The lines that build ``target`` from its fields, which each of its entrances repeats."""
    if target.type == "axes":
        return _axes_lines(target)
    if target.type == "function_plot":
        return _plot_lines(target)
    if target.type == "math_formula":
        return _written_lines(target, mobject="MathTex")
    if target.type == "text":
        return _written_lines(target, mobject="Text")
    if target.type == "three_d_axes":
        return _three_d_axes_lines(target)
    if target.type == "surface":
        return _surface_lines(target)
    if target.type == "parametric_curve":
        return _curve_lines(target)
    if target.type in SHAPE_TYPES:
        return _shape_lines(target)
    raise CodegenError(f'unknown object type "{target.type}"')


def _axes_lines(target: SceneObject) -> list[str]:
    params = target.params
    lines = [
        f"        {target.id} = Axes(",
        f"            x_range={_list(params['xRange'])},",
        f"            y_range={_list(params['yRange'])},",
        f"            x_length={_number(params['xLength'])},",
        f"            y_length={_number(params['yLength'])},",
        f'            axis_config={{"color": "{params["color"]}"}},',
        "        )",
    ]
    if params.get("showLabels") is True:
        lines.append(f'        {target.id}_labels = {target.id}.get_axis_labels(x_label="x", y_label="y")')
        lines.append(f"        {target.id}_group = VGroup({target.id}, {target.id}_labels)")
    return lines


def _three_d_axes_lines(target: SceneObject) -> list[str]:
    params = target.params
    lines = [
        f"        {target.id} = ThreeDAxes(",
        f"            x_range={_list(params['xRange'])},",
        f"            y_range={_list(params['yRange'])},",
        f"            z_range={_list(params['zRange'])},",
    ]
    for key, argument in (("xLength", "x_length"), ("yLength", "y_length"), ("zLength", "z_length")):
        if key in params:
            lines.append(f"            {argument}={_number(params[key])},")
    lines.append("        )")
    return lines


def _surface_lines(target: SceneObject) -> list[str]:
    params = target.params
    expression = params["expression"]
    if not isinstance(expression, str):
        raise CodegenError("surface expression must be a string")
    try:
        body = expression_to_python(expression, SURFACE_VARIABLES, {"x": "u", "y": "v"})
    except DocumentError as exc:
        raise CodegenError(str(exc)) from exc
    resolution = params["resolution"]
    if not isinstance(resolution, list) or len(resolution) != 2:
        raise CodegenError("surface resolution must be two integers")
    axes_id = params["axesId"]
    return [
        f"        {target.id} = Surface(",
        f"            lambda u, v: {axes_id}.c2p(u, v, {body}),",
        f"            u_range={_list(params['uRange'])},",
        f"            v_range={_list(params['vRange'])},",
        f"            resolution=({_number(resolution[0])}, {_number(resolution[1])}),",
        f'            fill_color="{params["color"]}",',
        "            checkerboard_colors=False,",
        "        )",
    ]


def _curve_lines(target: SceneObject) -> list[str]:
    params = target.params
    components = params["components"]
    if not isinstance(components, list) or len(components) != 3:
        raise CodegenError("curve components must be three expressions")
    expressions = [item for item in components if isinstance(item, str)]
    if len(expressions) != 3:
        raise CodegenError("curve components must be three expressions")
    try:
        bodies = [expression_to_python(item, CURVE_VARIABLES) for item in expressions]
    except DocumentError as exc:
        raise CodegenError(str(exc)) from exc
    axes_id = params["axesId"]
    return [
        f"        {target.id} = ParametricFunction(",
        f"            lambda t: {axes_id}.c2p({bodies[0]}, {bodies[1]}, {bodies[2]}),",
        f"            t_range={_list(params['tRange'])},",
        f'            color="{params["color"]}",',
        "        )",
    ]


def _plot_lines(target: SceneObject) -> list[str]:
    params = target.params
    expression = params["expression"]
    if not isinstance(expression, str):
        raise CodegenError("plot expression must be a string")
    try:
        body = expression_to_python(expression, PLOT_VARIABLES)
    except DocumentError as exc:
        raise CodegenError(str(exc)) from exc
    axes_id = params["axesId"]
    return [
        f"        {target.id} = {axes_id}.plot(",
        f"            lambda x: {body},",
        f"            x_range={_list([params['xMin'], params['xMax']])},",
        f'            color="{params["color"]}",',
        "        )",
    ]


def _written_lines(target: SceneObject, mobject: str) -> list[str]:
    params = target.params
    if mobject == "MathTex":
        latex = params["latex"]
        if not isinstance(latex, str):
            raise CodegenError("formula latex must be a string")
        literal = _raw_string(latex)
    else:
        text = params["text"]
        if not isinstance(text, str):
            raise CodegenError("text must be a string")
        literal = _text_literal(text)
    return [
        f'        {target.id} = {mobject}({literal}, color="{params["color"]}")',
        *_position_lines(target.id, params),
    ]


def _shape_lines(target: SceneObject) -> list[str]:
    """A shape, at its fields. One colour is its stroke and its fill."""
    params = target.params
    color = f'color="{params["color"]}"'
    if target.type == "dot":
        return [f"        {target.id} = Dot({_point(params['center'])}, radius={_number(params['radius'])}, {color})"]
    if target.type == "axes_point":
        point = f"{params['axesId']}.c2p({_number(params['x'])}, {_number(params['y'])})"
        return [f"        {target.id} = Dot({point}, radius={_number(params['radius'])}, {color})"]
    stroke = f"stroke_width={_number(params['strokeWidth'])}"
    if target.type in {"line", "arrow"}:
        ends = f"{_point(params['start'])}, {_point(params['end'])}"
        # Without buff=0 Manim's arrow leaves a gap at both ends.
        shape = (
            f"Line({ends}, {color}, {stroke})" if target.type == "line" else f"Arrow({ends}, buff=0, {color}, {stroke})"
        )
        return [f"        {target.id} = {shape}"]
    fill = f"fill_opacity={_number(params['fillOpacity'])}"
    if target.type == "circle":
        shape = f"Circle(radius={_number(params['radius'])}, {color}, {stroke}, {fill})"
    elif target.type == "rectangle":
        size = f"width={_number(params['width'])}, height={_number(params['height'])}"
        shape = f"Rectangle({size}, {color}, {stroke}, {fill})"
    else:
        raise CodegenError(f'unknown shape "{target.type}"')
    # The centre is a field, not an offset, so the shape is always moved to it.
    return [f"        {target.id} = {shape}", f"        {target.id}.move_to({_point(params['center'])})"]


def _position_lines(variable: str, params: Mapping[str, JsonValue]) -> list[str]:
    anchor = params.get("anchor", "origin")
    if not isinstance(anchor, str) or anchor not in _ANCHOR_CALL:
        raise CodegenError(f"unknown anchor {anchor!r}")
    lines = [f"        {variable}.{_ANCHOR_CALL[anchor]}"]
    shift = params.get("shift", [0, 0])
    if not isinstance(shift, list) or len(shift) != 2:
        raise CodegenError("shift must be two numbers")
    if _float(shift[0]) != 0.0 or _float(shift[1]) != 0.0:
        lines.append(f"        {variable}.shift({_point(shift)})")
    return lines


def _point(value: JsonValue) -> str:
    """A point or an offset ``[x, y]`` of the scene plane, as Manim takes it."""
    if not isinstance(value, list) or len(value) != 2:
        raise CodegenError("a point must be two numbers")
    return f"np.array([{_shift_component(value[0])}, {_shift_component(value[1])}, 0.0])"


def _raw_string(value: str) -> str:
    """A raw string literal for plain LaTeX, otherwise ``repr``, which escapes anything a raw string cannot hold.

    ``isprintable`` is false for line breaks, tab, NUL and the other controls.
    """
    if value.isprintable() and '"' not in value and not value.endswith("\\"):
        return 'r"' + value + '"'
    return repr(value)


def _text_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _list(value: object) -> str:
    if not isinstance(value, list):
        raise CodegenError("expected a list of numbers")
    return "[" + ", ".join(_number(item) for item in value) + "]"


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CodegenError(f"expected a number, got {value!r}")
    return float(value)


def _number(value: object) -> str:
    """``value`` at the three decimals load judges it at (D02), a whole number as an integer."""
    quantized = quantize(_float(value))
    if isinstance(quantized, int):
        return str(quantized)
    return f"{quantized:.3f}".rstrip("0").rstrip(".")


def _shift_component(value: object) -> str:
    rounded = round(_float(value), 3)
    text = f"{rounded:.3f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    if text in {"-0.0", "-0."}:
        return "0.0"
    return text
