"""The beat catalog, the easings and the shapes: one table that every reader shares.

Parsing and validation (``document.py``, ``timeline.py``), codegen, the edit
defaults and the UI's typed copy (``tests/fixtures/protocol/beat-kinds.json``)
read these rows. This module holds data only and imports nothing from the
document model, so the model can import it.

A beat compiles to exactly one ``self.play`` or ``self.wait``. Its *family*
says what it does to the on-screen state of each target: an entrance brings
it on, an exit takes it off, a change or an emphasis needs it on and leaves it
on, a morph takes its source off and brings its destination on. A kind of
arity ``some`` acts on one or more targets in one play; a kind of arity
``pair`` takes exactly two, in the order of its roles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mathimator_worker.jsonio import JsonValue

Family = Literal["hold", "entrance", "exit", "change", "emphasis", "morph", "camera"]
Arity = Literal["none", "some", "pair"]
ParamType = Literal["number", "vector", "color", "id"]
State = Literal["on", "off"]
Centre = Literal["own", "shared", "axes"]


@dataclass(frozen=True)
class Easing:
    """A Manim rate function that ``from manim import *`` exports, and where it leaves the animation."""

    name: str
    label: str
    # The value at t = 1: 1 for an easing that arrives, 0 for one that comes back.
    ends_at: Literal[0, 1]


@dataclass(frozen=True)
class Param:
    """One setting of a beat kind or a shape, as stored, judged and shown."""

    name: str
    label: str
    type: ParamType
    # What an edit stores when it names no value; it copies a list first. None: the edit computes it.
    default: JsonValue
    # Inclusive bounds of a number or of each component of a vector, judged at three decimals (D02).
    low: float | None = None
    high: float | None = None
    # Greater than 0 at three decimals, with the message of every positive field.
    positive: bool = False


@dataclass(frozen=True)
class Role:
    """One of the two targets of a pair kind."""

    name: str
    label: str
    # The verb of a refusal that names this target.
    verb: str
    # The role in a refusal that counts the targets: "a source", "an object".
    noun: str
    types: frozenset[str]
    before: State
    after: State


@dataclass(frozen=True)
class BeatKind:
    action: str
    family: Family
    label: str
    arity: Arity
    # The verb of a refusal that names a target of a kind of arity "some".
    verb: str = ""
    # The object types a kind of arity "some" accepts.
    types: frozenset[str] = frozenset()
    roles: tuple[Role, ...] = ()
    params: tuple[Param, ...] = ()
    # The easings a beat of this kind may name; empty for a kind without easing.
    easings: frozenset[str] = frozenset()
    # What an absent easing means.
    default_easing: str | None = None
    # The rate function the emitted animation has when the play passes none.
    # Codegen names the default easing when the two differ.
    manim_default: str | None = None
    # Manim runs the rate function backwards, so codegen emits the mirror image.
    reversed_rate: bool = False
    # shared: two or more targets act about their common centre;
    # axes: targets drawn on axes that are also targets act about the axes' centre.
    centre: Centre = "own"
    # Codegen saves each target before the play and restores it after.
    restore: bool = False
    # The UI checks the on-screen children of axes along with them.
    with_children: bool = False
    # False: a state it leaves could change Manim's key for a later play although the document did not
    # change, so while Manim runs the supervisor takes no new partial for a beat after the first one.
    predictable: bool = True
    # The scene kinds that offer it.
    scenes: frozenset[str] = frozenset({"scene", "three_d_scene"})
    # The animation of one target, ``{v}`` standing for its variable and each setting for its text
    # (a vector ``shift`` as ``{shift_x}`` and ``{shift_y}``); for a pair, the one animation, each
    # role name standing for its target's variable.
    call: str | None = None
    # The animation of a target that acts about the centre ``{c}`` (``centre`` above).
    centred_call: str | None = None
    # The variable of a target is its bare id, not the group of labelled axes.
    bare: bool = False
    # A refusal kept word for word for a target of the wrong type; ``{path}`` and ``{id}`` are filled in.
    type_refusal: str | None = None


@dataclass(frozen=True)
class Shape:
    """A shape object type, added from the Add menu with its entrance beat."""

    type: str
    # The Add menu label.
    label: str
    id_prefix: str
    # The name of a new object.
    name: str
    # The action of its entrance beat.
    entrance: str
    params: tuple[Param, ...]


EASINGS: dict[str, Easing] = {
    easing.name: easing
    for easing in (
        Easing("smooth", "Smooth", 1),
        Easing("linear", "Linear", 1),
        Easing("rush_into", "Rush in", 1),
        Easing("rush_from", "Rush out", 1),
        Easing("slow_into", "Slow into", 1),
        Easing("double_smooth", "Double smooth", 1),
        Easing("lingering", "Lingering (arrives early, holds)", 1),
        Easing("there_and_back", "There and back", 0),
        Easing("there_and_back_with_pause", "There and back, with a pause", 0),
    )
}
# The mirror image 1 - f(1 - t) of each easing that has one among the exported easings.
MIRROR = {
    "smooth": "smooth",
    "linear": "linear",
    "rush_into": "rush_from",
    "rush_from": "rush_into",
    "double_smooth": "double_smooth",
}
PATH = frozenset(name for name, easing in EASINGS.items() if easing.ends_at == 1)
RETURN = frozenset(name for name, easing in EASINGS.items() if easing.ends_at == 0)
ALL_EASINGS = PATH | RETURN
MIRRORED = frozenset(MIRROR)

FAMILIES: dict[Family, str] = {
    "hold": "Wait",
    "entrance": "Entrances",
    "exit": "Exits",
    "change": "Changes",
    "emphasis": "Emphasis",
    "morph": "Morphs",
    "camera": "Camera",
}
# The on-screen state a target of each family needs before the beat and has after it (arity "some").
FAMILY_STATES: dict[Family, tuple[State, State]] = {
    "entrance": ("off", "on"),
    "exit": ("on", "off"),
    "change": ("on", "on"),
    "emphasis": ("on", "on"),
}

SCENE_ONLY = frozenset({"scene"})
THREE_D_ONLY = frozenset({"three_d_scene"})

_CENTER = Param("center", "Center", "vector", [0, 0], -100, 100)
_START = Param("start", "Start", "vector", [-2, 0], -100, 100)
_END = Param("end", "End", "vector", [2, 0], -100, 100)
_DOT_RADIUS = Param("radius", "Radius", "number", 0.08, 0.001, 50)
_WARM = Param("color", "Color", "color", "#E07A5F")
_WHITE = Param("color", "Color", "color", "#FFFFFF")
_BLUE = Param("color", "Color", "color", "#58C4DD")
_STROKE = Param("strokeWidth", "Stroke width", "number", 4, 0, 50)
_FILL = Param("fillOpacity", "Fill opacity", "number", 0, 0, 1)

SHAPES: dict[str, Shape] = {
    shape.type: shape
    for shape in (
        Shape("dot", "Dot", "dot", "Dot", "grow", (_CENTER, _DOT_RADIUS, _WARM)),
        Shape("line", "Line", "line", "Line", "create", (_START, _END, _WHITE, _STROKE)),
        Shape(
            "arrow",
            "Arrow",
            "arrow",
            "Arrow",
            "grow_arrow",
            (
                Param("start", "Start", "vector", [-2, -1], -100, 100),
                Param("end", "End", "vector", [2, 1], -100, 100),
                _WHITE,
                Param("strokeWidth", "Stroke width", "number", 6, 0, 50),
            ),
        ),
        Shape(
            "circle",
            "Circle",
            "circle",
            "Circle",
            "create",
            (_CENTER, Param("radius", "Radius", "number", 1, 0.001, 50), _BLUE, _STROKE, _FILL),
        ),
        Shape(
            "rectangle",
            "Rectangle",
            "rectangle",
            "Rectangle",
            "create",
            (
                _CENTER,
                Param("width", "Width", "number", 4, 0.001, 100),
                Param("height", "Height", "number", 2, 0.001, 100),
                _BLUE,
                _STROKE,
                _FILL,
            ),
        ),
        Shape(
            "axes_point",
            "Point on axes",
            "point",
            "Point",
            "grow",
            (
                # The edit picks the axes and the middle of their ranges.
                Param("axesId", "Axes", "id", None),
                Param("x", "x", "number", None, -1_000_000, 1_000_000),
                Param("y", "y", "number", None, -1_000_000, 1_000_000),
                _DOT_RADIUS,
                _WARM,
            ),
        ),
    )
}

# Object types in the order the catalog lists them.
OBJECT_TYPE_ORDER = (
    "axes",
    "function_plot",
    "math_formula",
    "text",
    *SHAPES,
    "three_d_axes",
    "surface",
    "parametric_curve",
)
SHAPE_TYPES = frozenset(SHAPES)
TWO_D_TYPES = frozenset({"axes", "function_plot", "math_formula", "text"}) | SHAPE_TYPES
THREE_D_TYPES = frozenset({"three_d_axes", "surface", "parametric_curve"})
ALL_TYPES = TWO_D_TYPES | THREE_D_TYPES
# The objects drawn on axes, which name them in ``axesId`` and need them on screen.
AXES_CHILD_TYPES = frozenset({"function_plot", "axes_point", "surface", "parametric_curve"})
WRITTEN = frozenset({"math_formula", "text"})
FORMULA = frozenset({"math_formula"})
DRAWN = ALL_TYPES - WRITTEN
FILLED = frozenset({"math_formula", "text", "dot", "axes_point", "circle", "rectangle"})
PATHS = frozenset({"function_plot", "line", "arrow", "circle", "rectangle"})
MOVERS = frozenset({"dot", "axes_point"})
# Scaling an arrow to nothing keeps its tip at full size, so it neither grows nor shrinks from a point.
SCALABLE = ALL_TYPES - {"arrow"}
SCALABLE_2D = TWO_D_TYPES - {"arrow"}

KINDS: dict[str, BeatKind] = {
    kind.action: kind
    for kind in (
        BeatKind("wait", "hold", "Wait", "none"),
        BeatKind(
            "create",
            "entrance",
            "Create",
            "some",
            "creates",
            DRAWN,
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            call="Create({v})",
            type_refusal="{path} creates {id}, but create is not its entrance",
        ),
        BeatKind(
            "fade_in",
            "entrance",
            "Fade in",
            "some",
            "fades in",
            ALL_TYPES,
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            call="FadeIn({v})",
        ),
        BeatKind(
            "write",
            "entrance",
            "Write",
            "some",
            "writes",
            WRITTEN,
            easings=PATH,
            # Every scene written before easings existed renders Write linearly.
            default_easing="linear",
            manim_default="linear",
            scenes=SCENE_ONLY,
            call="Write({v})",
            type_refusal="{path} writes {id}, but only formulas and text are written",
        ),
        BeatKind(
            "grow",
            "entrance",
            "Grow from center",
            "some",
            "grows",
            SCALABLE,
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            call="GrowFromCenter({v})",
        ),
        BeatKind(
            "grow_arrow",
            "entrance",
            "Grow arrow",
            "some",
            "grows",
            frozenset({"arrow"}),
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            scenes=SCENE_ONLY,
            call="GrowArrow({v})",
        ),
        BeatKind(
            "draw_border_then_fill",
            "entrance",
            "Draw border, then fill",
            "some",
            "draws",
            FILLED,
            easings=PATH,
            default_easing="smooth",
            manim_default="double_smooth",
            scenes=SCENE_ONLY,
            call="DrawBorderThenFill({v})",
        ),
        BeatKind(
            "spin_in",
            "entrance",
            "Spin in",
            "some",
            "spins in",
            SCALABLE_2D,
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            scenes=SCENE_ONLY,
            call="SpinInFromNothing({v})",
        ),
        BeatKind(
            "fade_out",
            "exit",
            "Fade out",
            "some",
            "fades out",
            ALL_TYPES,
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            with_children=True,
            call="FadeOut({v})",
        ),
        BeatKind(
            "uncreate",
            "exit",
            "Uncreate",
            "some",
            "uncreates",
            DRAWN,
            easings=MIRRORED,
            default_easing="smooth",
            manim_default="smooth",
            reversed_rate=True,
            with_children=True,
            call="Uncreate({v})",
        ),
        BeatKind(
            "unwrite",
            "exit",
            "Unwrite",
            "some",
            "unwrites",
            WRITTEN,
            easings=MIRRORED,
            default_easing="smooth",
            manim_default="linear",
            reversed_rate=True,
            with_children=True,
            scenes=SCENE_ONLY,
            call="Unwrite({v})",
        ),
        BeatKind(
            "shrink",
            "exit",
            "Shrink away",
            "some",
            "shrinks",
            SCALABLE,
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            with_children=True,
            call="ShrinkToCenter({v}, remover=True)",
        ),
        BeatKind(
            "move",
            "change",
            "Move by",
            "some",
            "changes",
            ALL_TYPES,
            params=(Param("shift", "Shift", "vector", [1, 0]),),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            with_children=True,
            call="{v}.animate.shift(np.array([{shift_x}, {shift_y}, 0.0]))",
        ),
        BeatKind(
            "move_to",
            "change",
            "Move to",
            "some",
            "moves",
            ALL_TYPES,
            params=(Param("point", "Point", "vector", [0, 0], -100, 100),),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            centre="shared",
            with_children=True,
            call="{v}.animate.move_to(np.array([{point_x}, {point_y}, 0.0]))",
            centred_call="{v}.animate.shift(np.array([{point_x}, {point_y}, 0.0]) - {c})",
        ),
        BeatKind(
            "scale",
            "change",
            "Scale",
            "some",
            "scales",
            ALL_TYPES,
            params=(Param("factor", "Factor", "number", 1.5, 0.01, 100),),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            centre="shared",
            with_children=True,
            call="{v}.animate.scale({factor})",
            centred_call="{v}.animate.scale({factor}, about_point={c})",
        ),
        BeatKind(
            "rotate",
            "change",
            "Rotate",
            "some",
            "rotates",
            ALL_TYPES,
            # Degrees, counter-clockwise.
            params=(Param("angle", "Angle", "number", 90, -3600, 3600),),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            centre="shared",
            with_children=True,
            # Rotate moves each point along an arc; .animate.rotate moves it in a straight line, so a whole
            # turn would not move at all.
            call="Rotate({v}, angle={angle} * DEGREES)",
            centred_call="Rotate({v}, angle={angle} * DEGREES, about_point={c})",
        ),
        BeatKind(
            "set_opacity",
            "change",
            "Set opacity",
            "some",
            "changes",
            ALL_TYPES,
            params=(Param("opacity", "Opacity", "number", 0.5, 0, 1),),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            # Curves, lines and outline shapes fade their stroke instead (codegen): a fill would show.
            call="{v}.animate.set_opacity({opacity})",
        ),
        BeatKind(
            "set_color",
            "change",
            "Recolor",
            "some",
            "changes",
            ALL_TYPES,
            params=(Param("color", "Color", "color", "#E07A5F"),),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            bare=True,
            call='{v}.animate.set_color("{color}")',
        ),
        BeatKind(
            "move_along",
            "change",
            "Move along a path",
            "pair",
            roles=(
                Role("mover", "Object", "moves", "an object", MOVERS, "on", "on"),
                Role("path", "Path", "moves along", "a path", PATHS, "on", "on"),
            ),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            scenes=SCENE_ONLY,
            call="MoveAlongPath({mover}, {path})",
        ),
        BeatKind(
            "indicate",
            "emphasis",
            "Indicate",
            "some",
            "indicates",
            ALL_TYPES,
            # Indicate scales and recolours the object itself: only a returning easing brings it back.
            easings=RETURN,
            default_easing="there_and_back",
            manim_default="there_and_back",
            centre="axes",
            with_children=True,
            call="Indicate({v})",
            # Indicate itself, about the axes' centre: Indicate takes no about-point.
            centred_call=(
                "Transform({v}, {v}.copy().scale(1.2, about_point={c}).set_color(PURE_YELLOW),"
                " rate_func=there_and_back)"
            ),
        ),
        BeatKind(
            "circumscribe",
            "emphasis",
            "Circumscribe",
            "some",
            "circumscribes",
            TWO_D_TYPES,
            # Its flash never runs backwards, so a returning easing would depend on the frame rate.
            easings=PATH,
            default_easing="smooth",
            manim_default="linear",
            scenes=SCENE_ONLY,
            call="Circumscribe({v})",
        ),
        BeatKind(
            "flash",
            "emphasis",
            "Flash",
            "some",
            "flashes",
            TWO_D_TYPES,
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="linear",
            scenes=SCENE_ONLY,
            call="Flash({v}, flash_radius=np.hypot({v}.width, {v}.height) / 2 + SMALL_BUFF)",
        ),
        BeatKind(
            "wiggle",
            "emphasis",
            "Wiggle",
            "some",
            "wiggles",
            ALL_TYPES,
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            centre="axes",
            restore=True,
            with_children=True,
            call="Wiggle({v})",
            centred_call="Wiggle({v}, scale_about_point={c}, rotate_about_point={c})",
        ),
        BeatKind(
            "focus",
            "emphasis",
            "Focus",
            "some",
            "focuses on",
            TWO_D_TYPES,
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            scenes=SCENE_ONLY,
            call="FocusOn({v})",
        ),
        BeatKind(
            "wave",
            "emphasis",
            "Wave",
            "some",
            "waves",
            TWO_D_TYPES,
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            with_children=True,
            scenes=SCENE_ONLY,
            call="ApplyWave({v})",
        ),
        BeatKind(
            "transform",
            "morph",
            "Transform",
            "pair",
            roles=(
                Role("source", "From", "transforms", "a source", TWO_D_TYPES, "on", "off"),
                Role("destination", "To", "brings", "a destination", TWO_D_TYPES, "off", "on"),
            ),
            easings=PATH,
            default_easing="smooth",
            manim_default="smooth",
            scenes=SCENE_ONLY,
            # A copy morphs, so the source itself is never changed and can enter again (codegen removes it first).
            call="ReplacementTransform({source}.copy(), {destination})",
        ),
        BeatKind(
            "transform_formula",
            "morph",
            "Transform formula (match symbols)",
            "pair",
            roles=(
                Role("source", "From", "transforms", "a source", FORMULA, "on", "off"),
                Role("destination", "To", "brings", "a destination", FORMULA, "off", "on"),
            ),
            easings=PATH,
            # Every scene written before easings existed renders this transform linearly.
            default_easing="linear",
            manim_default="linear",
            scenes=SCENE_ONLY,
            # Manim puts the source back as it was and removes it when the transform ends.
            call="TransformMatchingTex({source}, {destination})",
            bare=True,
            type_refusal="{path} transforms something other than a formula",
        ),
        BeatKind(
            "move_camera",
            "camera",
            "Move camera",
            "none",
            # The rules of scene.camera, so that the camera in effect is always a valid default.
            params=(
                Param("phi", "Phi", "number", None),
                Param("theta", "Theta", "number", None),
                Param("zoom", "Zoom", "number", None, positive=True),
            ),
            easings=ALL_EASINGS,
            default_easing="smooth",
            manim_default="smooth",
            predictable=False,
            scenes=THREE_D_ONLY,
        ),
    )
}

# The entrance an object takes when it is added or when "+ Beat" brings it on.
NATURAL_ENTRANCE = {
    "axes": "create",
    "function_plot": "create",
    "math_formula": "write",
    "text": "write",
    **{shape.type: shape.entrance for shape in SHAPES.values()},
    "three_d_axes": "create",
    "surface": "create",
    "parametric_curve": "create",
}


def states(kind: BeatKind, count: int) -> list[tuple[State, State]]:
    """The state each of ``count`` targets of ``kind`` needs before the beat, and has after it."""
    if kind.arity == "pair":
        return [(role.before, role.after) for role in kind.roles[:count]]
    if kind.family in FAMILY_STATES:
        return [FAMILY_STATES[kind.family]] * count
    return []


def effective_easing(kind: BeatKind, easing: str | None) -> str | None:
    """The easing a beat runs with: the one it names, else its kind's default."""
    return easing if easing is not None else kind.default_easing
