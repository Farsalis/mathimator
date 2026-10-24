"""project.json model, validation, and cache identity.

``schemaVersion`` 1 is the only version this phase reads. A ``start`` field,
an unknown key, or any other schema version is refused. Beat start times are
derived and are not stored.

Every refusal is a DocumentError with a protocol code, and its message and
``field`` name the JSON path of the failing value. Numbers are judged at the
three decimals codegen emits (D02), so a step or a range that rounds to
nothing is refused instead of reaching Manim. The value on disk is kept as
written.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import keyword
import math
import re
from dataclasses import dataclass, field
from typing import NoReturn, TypeIs

from mathimator_worker.document_error import DocumentError
from mathimator_worker.expr import assert_supported
from mathimator_worker.jsonio import JsonValue, canonical_dumps, dumps_document

MANIM_VERSION = "0.22.0"
SCHEMA_VERSION = 1
GENERATOR_VERSION = 1
MIN_BEAT_SECONDS = 0.1
MAX_TOTAL_SECONDS = 120.0
MAX_TICKS_PER_AXIS = 500
# Manim samples a curve every 0.01 of t, so a span of 50 is 5000 samples:
# as many as a plot gets from 500 x steps of ten samples each (D04).
MAX_CURVE_SPAN = 50
MAX_LATEX_LENGTH = 500
MAX_TEXT_LENGTH = 200
MAX_SEED = 2**32 - 1
NOT_UTF8 = "project.json must be saved as UTF-8 text."

PREVIEW_WIDTH = 854
PREVIEW_HEIGHT = 480
PREVIEW_FPS = 15

EXPORT_FORMATS = frozenset({"mp4", "gif", "webm", "png_sequence"})
EXPORT_SIZES = frozenset({(854, 480), (1280, 720), (1920, 1080)})
EXPORT_FPS = frozenset({15, 30, 60})

ANCHORS = frozenset({"origin", "edge_up", "edge_down", "edge_left", "edge_right"})
PHASE_2_OBJECT_TYPES = frozenset({"axes", "function_plot", "math_formula", "text"})
THREE_D_OBJECT_TYPES = frozenset({"three_d_axes", "surface", "parametric_curve"})
OBJECT_TYPES = PHASE_2_OBJECT_TYPES | THREE_D_OBJECT_TYPES
ACTIONS = frozenset({"create", "fade_in", "write", "wait", "transform_formula", "set_color", "move"})
ENTRANCES = frozenset({"create", "fade_in", "write", "transform_formula"})
CREATE_TYPES = frozenset({"axes", "function_plot", "three_d_axes", "surface", "parametric_curve"})
WRITE_TYPES = frozenset({"math_formula", "text"})
TEMPLATE_IDS = frozenset({"axes_function", "formula_transform", "three_d_axes"})
MAX_RESOLUTION = 24
SURFACE_VARIABLES = frozenset({"x", "y"})
CURVE_VARIABLES = frozenset({"t"})
PLOT_VARIABLES = frozenset({"x"})

_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Lone surrogates, NUL and every C0 control except tab, LF and CR (D06). CR
# stays so that CRLF text written by Windows tools opens.
_UNSTORABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")

Number = int | float

_PROJECT_KEYS = {
    "schemaVersion",
    "generatorVersion",
    "id",
    "name",
    "seed",
    "scene",
    "previewProfile",
    "exportProfile",
}
_SCENE_KEYS = {"id", "name", "kind", "background", "objects", "beats", "templateInstances"}
_CAMERA_KEYS = {"phi", "theta", "zoom"}
_OBJECT_KEYS = {"id", "name", "type", "params"}
_BEAT_KEYS = {"id", "duration", "action", "targets", "params"}
_TEMPLATE_KEYS = {"id", "templateId", "params", "objectIds", "beatIds"}
_PREVIEW_KEYS = {"width", "height", "fps", "transparent"}
_EXPORT_KEYS = {"format", "width", "height", "fps", "transparent"}
_AXES_KEYS = {"xRange", "yRange", "xLength", "yLength", "color", "showLabels"}
_PLOT_KEYS = {"axesId", "expression", "xMin", "xMax", "color"}
_FORMULA_KEYS = {"latex", "color", "anchor", "shift"}
_TEXT_KEYS = {"text", "color", "anchor", "shift"}
_AXES_TEMPLATE_KEYS = {"expression", "xRange", "yRange", "color"}
_FORMULA_TEMPLATE_KEYS = {
    "sourceLatex",
    "destinationLatex",
    "sourceColor",
    "destinationColor",
    "anchor",
    "shift",
    "sourceAnchor",
    "destinationAnchor",
    "sourceShift",
    "destinationShift",
}
_FORMULA_TEMPLATE_REQUIRED = {
    "sourceLatex",
    "destinationLatex",
    "sourceColor",
    "destinationColor",
    "anchor",
    "shift",
}
_GENERATED_NAMES = frozenset(
    {
        "np",
        "self",
        "config",
        "MainScene",
        "Scene",
        "ThreeDScene",
        "Axes",
        "ThreeDAxes",
        "Surface",
        "ParametricFunction",
        "MathTex",
        "Text",
        "VGroup",
        "Create",
        "FadeIn",
        "Write",
        "TransformMatchingTex",
        "ORIGIN",
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
        "DEGREES",
    }
)
_THREE_D_TEMPLATE_KEYS = {
    "mode",
    "xRange",
    "yRange",
    "zRange",
    "phi",
    "theta",
    "zoom",
    "expression",
    "uRange",
    "vRange",
    "resolution",
    "surfaceColor",
    "components",
    "tRange",
    "curveColor",
}
_THREE_D_AXES_KEYS = {"xRange", "yRange", "zRange", "xLength", "yLength", "zLength"}
_SURFACE_KEYS = {"axesId", "expression", "uRange", "vRange", "resolution", "color"}
_CURVE_KEYS = {"axesId", "components", "tRange", "color"}
_SET_COLOR_KEYS = {"color"}
_MOVE_KEYS = {"shift"}

_AXES_ORDER = ("xRange", "yRange", "xLength", "yLength", "color", "showLabels")
_PLOT_ORDER = ("axesId", "expression", "xMin", "xMax", "color")
_FORMULA_ORDER = ("latex", "color", "anchor", "shift")
_TEXT_ORDER = ("text", "color", "anchor", "shift")
_THREE_D_AXES_ORDER = ("xRange", "yRange", "zRange", "xLength", "yLength", "zLength")
_SURFACE_ORDER = ("axesId", "expression", "uRange", "vRange", "resolution", "color")
_CURVE_ORDER = ("axesId", "components", "tRange", "color")
_TEMPLATE_ORDERS = {
    "axes_function": ("expression", "xRange", "yRange", "color"),
    "formula_transform": (
        "sourceLatex",
        "destinationLatex",
        "sourceColor",
        "destinationColor",
        "anchor",
        "shift",
        "sourceAnchor",
        "destinationAnchor",
        "sourceShift",
        "destinationShift",
    ),
    "three_d_axes": (
        "mode",
        "xRange",
        "yRange",
        "zRange",
        "phi",
        "theta",
        "zoom",
        "expression",
        "uRange",
        "vRange",
        "resolution",
        "surfaceColor",
        "components",
        "tRange",
        "curveColor",
    ),
}
_PARAM_ORDER = {
    "axes": _AXES_ORDER,
    "function_plot": _PLOT_ORDER,
    "math_formula": _FORMULA_ORDER,
    "text": _TEXT_ORDER,
    "three_d_axes": _THREE_D_AXES_ORDER,
    "surface": _SURFACE_ORDER,
    "parametric_curve": _CURVE_ORDER,
}


@dataclass
class Profile:
    width: int
    height: int
    fps: int
    transparent: bool


@dataclass
class ExportProfile:
    format: str
    width: int
    height: int
    fps: int
    transparent: bool


@dataclass
class SceneObject:
    id: str
    name: str
    type: str
    params: dict[str, JsonValue]


@dataclass
class Beat:
    id: str
    duration: Number
    action: str
    targets: list[str]
    params: dict[str, JsonValue] | None = None


@dataclass
class TemplateInstance:
    id: str
    template_id: str
    params: dict[str, JsonValue]
    object_ids: list[str]
    beat_ids: list[str]


@dataclass
class Camera:
    phi: Number
    theta: Number
    zoom: Number


@dataclass
class Scene:
    id: str
    name: str
    kind: str
    background: str
    objects: list[SceneObject] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)
    template_instances: list[TemplateInstance] = field(default_factory=list)
    camera: Camera | None = None


@dataclass
class Project:
    schema_version: int
    generator_version: int
    id: str
    name: str
    seed: int
    scene: Scene
    preview_profile: Profile
    export_profile: ExportProfile


def empty_project(name: str = "Untitled") -> Project:
    """An interactive project with no objects and no beats."""
    return Project(
        schema_version=SCHEMA_VERSION,
        generator_version=GENERATOR_VERSION,
        id="untitled",
        name=name,
        seed=1,
        scene=Scene(
            id="scene_main",
            name="Main",
            kind="scene",
            background="#000000",
        ),
        preview_profile=Profile(PREVIEW_WIDTH, PREVIEW_HEIGHT, PREVIEW_FPS, False),
        export_profile=ExportProfile("mp4", 1920, 1080, 30, False),
    )


def load_bytes(data: bytes) -> Project:
    """Decode project.json bytes and return a project, or raise DocumentError.

    One leading UTF-8 byte order mark is dropped. Any other encoding, such as
    the UTF-16 that Windows PowerShell 5 writes, is refused (D07). JSON in
    UTF-8 never holds a NUL byte, so one marks UTF-16 without a byte order
    mark, which ASCII text would otherwise pass as UTF-8.
    """
    if b"\x00" in data:
        raise DocumentError(NOT_UTF8, code="not_utf8")
    try:
        text = data.removeprefix(codecs.BOM_UTF8).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentError(NOT_UTF8, code="not_utf8") from exc
    return load_text(text)


def load_text(text: str) -> Project:
    """Parse ``text`` and return a project, or raise DocumentError.

    A number too large for a float, ``NaN`` and ``Infinity`` are invalid JSON.
    """
    try:
        raw = json.loads(text, parse_int=_finite_int, parse_float=_finite_float, parse_constant=_not_a_number)
    except json.JSONDecodeError as exc:
        raise _invalid_json(f"{exc.msg} at line {exc.lineno} column {exc.colno}") from exc
    except ValueError as exc:
        raise _invalid_json(str(exc)) from exc
    except RecursionError as exc:
        raise _invalid_json("it is nested too deeply") from exc
    if not isinstance(raw, dict):
        raise DocumentError("project.json must be a JSON object")
    if "schemaVersion" not in raw:
        raise DocumentError("schemaVersion is missing", field="schemaVersion")
    version = raw["schemaVersion"]
    if not _is_int(version) or version != SCHEMA_VERSION:
        raise DocumentError(f"schemaVersion {_shown(version)} is not supported", field="schemaVersion")
    project = _parse_project(raw)
    # A repeated id would otherwise surface as a broken template link.
    _check_identity(project)
    _rewrite_templates(project)
    validate_project(project)
    return project


def dumps_project(project: Project) -> str:
    """Return the stable on-disk bytes, including the trailing newline."""
    return dumps_document(project_dict(project))


def project_dict(project: Project) -> dict[str, JsonValue]:
    scene = project.scene
    scene_payload: dict[str, JsonValue] = {
        "id": scene.id,
        "name": scene.name,
        "kind": scene.kind,
        "background": scene.background,
    }
    if scene.camera is not None:
        scene_payload["camera"] = {
            "phi": scene.camera.phi,
            "theta": scene.camera.theta,
            "zoom": scene.camera.zoom,
        }
    scene_payload["objects"] = [_object_dict(item) for item in scene.objects]
    scene_payload["beats"] = [_beat_dict(item) for item in scene.beats]
    scene_payload["templateInstances"] = [_template_dict(item) for item in scene.template_instances]
    return {
        "schemaVersion": project.schema_version,
        "generatorVersion": project.generator_version,
        "id": project.id,
        "name": project.name,
        "seed": project.seed,
        "scene": scene_payload,
        "previewProfile": {
            "width": project.preview_profile.width,
            "height": project.preview_profile.height,
            "fps": project.preview_profile.fps,
            "transparent": project.preview_profile.transparent,
        },
        "exportProfile": {
            "format": project.export_profile.format,
            "width": project.export_profile.width,
            "height": project.export_profile.height,
            "fps": project.export_profile.fps,
            "transparent": project.export_profile.transparent,
        },
    }


def validate_project(project: Project) -> None:
    """Raise DocumentError when ``project`` breaks the section 4 rules."""
    _check_identity(project)
    _check_limits(project)
    _check_scene_kind(project)
    _check_timeline(project)
    _check_templates(project)


def derived_starts(durations: list[Number]) -> list[float]:
    """Start of each beat. Beat 0 starts at 0. Nothing here is stored."""
    starts: list[float] = []
    cursor = 0.0
    for duration in durations:
        starts.append(round(cursor, 3))
        cursor += float(duration)
    return starts


def total_duration(project: Project) -> float:
    return round(sum(float(beat.duration) for beat in project.scene.beats), 3)


def quantize(value: float) -> Number:
    rounded = round(float(value), 3)
    nearest = round(rounded)
    if abs(rounded - nearest) < 1e-9:
        return int(nearest)
    return rounded


def object_by_id(project: Project, object_id: str, field: str) -> SceneObject:
    for item in project.scene.objects:
        if item.id == object_id:
            return item
    raise DocumentError(f"unknown object {object_id}", field=field)


def beat_by_id(project: Project, beat_id: str, field: str) -> Beat:
    for beat in project.scene.beats:
        if beat.id == beat_id:
            return beat
    raise DocumentError(f"unknown beat {beat_id}", field=field)


def used_ids(project: Project) -> set[str]:
    scene = project.scene
    found = {project.id, scene.id}
    found.update(item.id for item in scene.objects)
    found.update(item.id for item in scene.beats)
    found.update(item.id for item in scene.template_instances)
    return found


def fresh_id(prefix: str, used: set[str]) -> str:
    number = 1
    while number < 10000:
        candidate = f"{prefix}_{number}"
        if candidate not in used and _ID.fullmatch(candidate):
            used.add(candidate)
            return candidate
        number += 1
    raise DocumentError("could not allocate an id")


def scene_stamp(project: Project) -> str:
    """Identity of the fields that dirty every beat, excluding display names.

    The camera is part of this stamp. Changing phi, theta, or zoom dirties
    every beat because the orientation call sits before the first ``play``.
    """
    camera = project.scene.camera
    camera_payload: dict[str, JsonValue] | None = None
    if camera is not None:
        camera_payload = {"phi": camera.phi, "theta": camera.theta, "zoom": camera.zoom}
    payload = {
        "background": project.scene.background,
        "camera": camera_payload,
        "generatorVersion": project.generator_version,
        "kind": project.scene.kind,
        "schemaVersion": project.schema_version,
        "seed": project.seed,
    }
    return _sha256(canonical_dumps(payload))


def beat_cache_keys(project: Project, profile: str) -> list[str]:
    return [
        _sha256(canonical_dumps(_beat_cache_payload(project, index, profile)))
        for index in range(len(project.scene.beats))
    ]


def scene_hash(project: Project) -> str:
    """Hash of schema, generator, seed, and scene. Display names are omitted.

    Names are not used in codegen and must not dirty a preview. Leaving them
    in this hash would change ``exportKey`` on a rename and throw away a
    finished export of the same movie.
    """
    payload = {
        "generatorVersion": project.generator_version,
        "schemaVersion": project.schema_version,
        "scene": _scene_without_names(project),
        "seed": project.seed,
    }
    return _sha256(canonical_dumps(payload))


def export_key(project: Project) -> str:
    profile = project.export_profile
    payload = {
        "exportProfile": {
            "format": profile.format,
            "fps": profile.fps,
            "height": profile.height,
            "transparent": profile.transparent,
            "width": profile.width,
        },
        "manimVersion": MANIM_VERSION,
        "sceneHash": scene_hash(project),
    }
    return _sha256(canonical_dumps(payload))


def export_folder_name(project: Project) -> str:
    return export_key(project)[:16]


def export_artifact_name(fmt: str) -> str:
    """File or directory name copied into ``.mathimator/exports/<exportKey>/``."""
    if fmt == "png_sequence":
        return "frames"
    if fmt == "mp4":
        return "scene.mp4"
    if fmt == "gif":
        return "scene.gif"
    if fmt == "webm":
        return "scene.webm"
    raise ValueError(f"unknown format {fmt}")


def parse_text(value: object, path: str, limit: int | None = None) -> str:
    """A string that can be saved, hashed and written into scene.py (D06).

    Load and every edit that stores text use this check. ``limit`` is the
    length cap of the field, if it has one (D04).
    """
    if not isinstance(value, str):
        raise DocumentError(f"{path} must be a string", field=path)
    if _UNSTORABLE.search(value) is not None:
        raise DocumentError(f"{path} contains a character Mathimator cannot store.", code="invalid_text", field=path)
    if limit is not None and len(value) > limit:
        raise DocumentError(f"{path} is longer than {limit} characters", code="limit_exceeded", field=path)
    return value


def parse_number(value: object, path: str) -> Number:
    """A JSON number that converts to a finite float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError(f"{path} must be a number", field=path)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise DocumentError(f"{path} must be a finite number", field=path)
    return value


def parse_axis_range(value: object, path: str) -> list[JsonValue]:
    """``[min, max, step]`` of an axis that draws at most 500 ticks (D02, D04).

    Load and edit share this check. It judges the numbers as codegen emits
    them and returns them as given.
    """
    numbers = _numbers(value, path, 3)
    low, high, step = numbers
    _check_greater(high, low, path, "min", "max")
    _check_greater(step, 0, path, "0", "step")
    intervals = (float(quantize(high)) - float(quantize(low))) / float(quantize(step))
    if intervals >= MAX_TICKS_PER_AXIS:
        raise DocumentError(f"{path} has more than {MAX_TICKS_PER_AXIS} ticks", code="limit_exceeded", field=path)
    return list[JsonValue](numbers)


def parse_color(value: object, path: str) -> str:
    if not isinstance(value, str) or _COLOR.fullmatch(value) is None:
        raise DocumentError(f"{path} must be #RRGGBB", field=path)
    return value.upper()


def parse_anchor(value: object, path: str) -> str:
    if not isinstance(value, str) or value not in ANCHORS:
        raise DocumentError(f"{path} {_shown(value)} is not an anchor", field=path)
    return value


def _parse_project(raw: dict[str, object]) -> Project:
    _check_keys(raw, _PROJECT_KEYS, "")
    _require(raw, _PROJECT_KEYS, "")
    generator = raw["generatorVersion"]
    if not _is_int(generator) or generator != GENERATOR_VERSION:
        raise DocumentError(f"generatorVersion {_shown(generator)} is not supported", field="generatorVersion")
    scene_raw = raw["scene"]
    if not isinstance(scene_raw, dict):
        raise DocumentError("scene must be an object", field="scene")
    seed = _int_value(raw["seed"], "seed")
    if not 0 <= seed <= MAX_SEED:
        raise DocumentError(f"seed must be from 0 to {MAX_SEED}", field="seed")
    return Project(
        schema_version=SCHEMA_VERSION,
        generator_version=GENERATOR_VERSION,
        id=_id_value(raw["id"], "id"),
        name=parse_text(raw["name"], "name"),
        seed=seed,
        scene=_parse_scene(scene_raw),
        preview_profile=_parse_preview(raw["previewProfile"]),
        export_profile=_parse_export(raw["exportProfile"]),
    )


def _parse_scene(raw: dict[str, object]) -> Scene:
    allowed = set(_SCENE_KEYS)
    if raw.get("kind") == "three_d_scene":
        allowed.add("camera")
    _check_keys(raw, allowed, "scene")
    _require(raw, _SCENE_KEYS, "scene")
    kind = raw["kind"]
    if not isinstance(kind, str) or kind not in {"scene", "three_d_scene"}:
        raise DocumentError(f"scene.kind {_shown(kind)} is not a scene kind", field="scene.kind")
    if kind == "three_d_scene" and "camera" not in raw:
        raise DocumentError("scene.camera is missing", field="scene.camera")
    objects_raw = raw["objects"]
    beats_raw = raw["beats"]
    templates_raw = raw["templateInstances"]
    if not isinstance(objects_raw, list):
        raise DocumentError("scene.objects must be an array", field="scene.objects")
    if not isinstance(beats_raw, list):
        raise DocumentError("scene.beats must be an array", field="scene.beats")
    if not isinstance(templates_raw, list):
        raise DocumentError("scene.templateInstances must be an array", field="scene.templateInstances")
    camera = None
    if kind == "three_d_scene":
        camera = _parse_camera(raw["camera"], "scene.camera")
    return Scene(
        id=_id_value(raw["id"], "scene.id"),
        name=parse_text(raw["name"], "scene.name"),
        kind=kind,
        background=parse_color(raw["background"], "scene.background"),
        objects=[_parse_object(item, index) for index, item in enumerate(objects_raw)],
        beats=[_parse_beat(item, index) for index, item in enumerate(beats_raw)],
        template_instances=[_parse_template(item, index) for index, item in enumerate(templates_raw)],
        camera=camera,
    )


def _parse_object(raw: object, index: int) -> SceneObject:
    path = f"scene.objects[{index}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object", field=path)
    _check_keys(raw, _OBJECT_KEYS, path)
    _require(raw, _OBJECT_KEYS, path)
    object_type = raw["type"]
    if not isinstance(object_type, str) or object_type not in OBJECT_TYPES:
        raise DocumentError(f"{path}.type {_shown(object_type)} is not an object type", field=f"{path}.type")
    params = raw["params"]
    if not isinstance(params, dict):
        raise DocumentError(f"{path}.params must be an object", field=f"{path}.params")
    object_id = _id_value(raw["id"], f"{path}.id")
    _reject_generated_name(object_id, f"{path}.id")
    return SceneObject(
        id=object_id,
        name=parse_text(raw["name"], f"{path}.name"),
        type=object_type,
        params=_parse_params(object_type, params, f"{path}.params"),
    )


def _parse_params(object_type: str, raw: dict[str, object], path: str) -> dict[str, JsonValue]:
    if object_type == "axes":
        _check_keys(raw, _AXES_KEYS, path)
        _require(raw, _AXES_KEYS, path)
        return {
            "xRange": parse_axis_range(raw["xRange"], f"{path}.xRange"),
            "yRange": parse_axis_range(raw["yRange"], f"{path}.yRange"),
            "xLength": _positive(raw["xLength"], f"{path}.xLength"),
            "yLength": _positive(raw["yLength"], f"{path}.yLength"),
            "color": parse_color(raw["color"], f"{path}.color"),
            "showLabels": _bool(raw["showLabels"], f"{path}.showLabels"),
        }
    if object_type == "function_plot":
        _check_keys(raw, _PLOT_KEYS, path)
        _require(raw, _PLOT_KEYS, path)
        expression = _expression(raw["expression"], f"{path}.expression", PLOT_VARIABLES)
        x_min = parse_number(raw["xMin"], f"{path}.xMin")
        x_max = parse_number(raw["xMax"], f"{path}.xMax")
        _check_greater(x_max, x_min, f"{path}.xMax", "xMin")
        return {
            "axesId": _id_value(raw["axesId"], f"{path}.axesId"),
            "expression": expression,
            "xMin": x_min,
            "xMax": x_max,
            "color": parse_color(raw["color"], f"{path}.color"),
        }
    if object_type == "math_formula":
        _check_keys(raw, _FORMULA_KEYS, path)
        _require(raw, {"latex", "color", "anchor"}, path)
        formula_params: dict[str, JsonValue] = {
            "latex": parse_text(raw["latex"], f"{path}.latex", MAX_LATEX_LENGTH),
            "color": parse_color(raw["color"], f"{path}.color"),
            "anchor": parse_anchor(raw["anchor"], f"{path}.anchor"),
        }
        if "shift" in raw:
            formula_params["shift"] = _shift(raw["shift"], f"{path}.shift")
        return formula_params
    if object_type == "text":
        _check_keys(raw, _TEXT_KEYS, path)
        _require(raw, {"text", "color", "anchor"}, path)
        text_params: dict[str, JsonValue] = {
            "text": parse_text(raw["text"], f"{path}.text", MAX_TEXT_LENGTH),
            "color": parse_color(raw["color"], f"{path}.color"),
            "anchor": parse_anchor(raw["anchor"], f"{path}.anchor"),
        }
        if "shift" in raw:
            text_params["shift"] = _shift(raw["shift"], f"{path}.shift")
        return text_params
    if object_type == "three_d_axes":
        _check_keys(raw, _THREE_D_AXES_KEYS, path)
        _require(raw, {"xRange", "yRange", "zRange"}, path)
        params: dict[str, JsonValue] = {
            "xRange": parse_axis_range(raw["xRange"], f"{path}.xRange"),
            "yRange": parse_axis_range(raw["yRange"], f"{path}.yRange"),
            "zRange": parse_axis_range(raw["zRange"], f"{path}.zRange"),
        }
        for key in ("xLength", "yLength", "zLength"):
            if key in raw:
                params[key] = _positive(raw[key], f"{path}.{key}")
        return params
    if object_type == "surface":
        _check_keys(raw, _SURFACE_KEYS, path)
        _require(raw, _SURFACE_KEYS, path)
        return {
            "axesId": _id_value(raw["axesId"], f"{path}.axesId"),
            "expression": _expression(raw["expression"], f"{path}.expression", SURFACE_VARIABLES),
            "uRange": _pair_range(raw["uRange"], f"{path}.uRange"),
            "vRange": _pair_range(raw["vRange"], f"{path}.vRange"),
            "resolution": parse_resolution(raw["resolution"], f"{path}.resolution"),
            "color": parse_color(raw["color"], f"{path}.color"),
        }
    if object_type == "parametric_curve":
        _check_keys(raw, _CURVE_KEYS, path)
        _require(raw, _CURVE_KEYS, path)
        return {
            "axesId": _id_value(raw["axesId"], f"{path}.axesId"),
            "components": _components(raw["components"], f"{path}.components"),
            "tRange": _curve_range(raw["tRange"], f"{path}.tRange"),
            "color": parse_color(raw["color"], f"{path}.color"),
        }
    raise DocumentError(f'unknown object type "{object_type}"')


def _parse_beat(raw: object, index: int) -> Beat:
    path = f"scene.beats[{index}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object", field=path)
    _check_keys(raw, _BEAT_KEYS, path)
    _require(raw, {"id", "duration", "action", "targets"}, path)
    action = raw["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise DocumentError(f"{path}.action {_shown(action)} is not an action", field=f"{path}.action")
    targets = _id_list(raw["targets"], f"{path}.targets")
    params: dict[str, JsonValue] | None = None
    if action == "set_color":
        params = _parse_color_params(raw.get("params"), f"{path}.params")
    elif action == "move":
        params = _parse_move_params(raw.get("params"), f"{path}.params")
    elif "params" in raw:
        raise DocumentError(f"{path}.params is not allowed for {action}", field=f"{path}.params")
    return Beat(
        id=_id_value(raw["id"], f"{path}.id"),
        duration=_duration(raw["duration"], f"{path}.duration"),
        action=action,
        targets=targets,
        params=params,
    )


def _parse_color_params(raw: object, path: str) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object", field=path)
    _check_keys(raw, _SET_COLOR_KEYS, path)
    _require(raw, _SET_COLOR_KEYS, path)
    return {"color": parse_color(raw["color"], f"{path}.color")}


def _parse_move_params(raw: object, path: str) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object", field=path)
    _check_keys(raw, _MOVE_KEYS, path)
    _require(raw, _MOVE_KEYS, path)
    return {"shift": _shift(raw["shift"], f"{path}.shift")}


def _parse_template(raw: object, index: int) -> TemplateInstance:
    path = f"scene.templateInstances[{index}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object", field=path)
    _check_keys(raw, _TEMPLATE_KEYS, path)
    _require(raw, _TEMPLATE_KEYS, path)
    template_id = raw["templateId"]
    if not isinstance(template_id, str) or template_id not in TEMPLATE_IDS:
        raise DocumentError(f"{path}.templateId {_shown(template_id)} is not a template", field=f"{path}.templateId")
    params_raw = raw["params"]
    if not isinstance(params_raw, dict):
        raise DocumentError(f"{path}.params must be an object", field=f"{path}.params")
    object_ids = _id_list(raw["objectIds"], f"{path}.objectIds")
    beat_ids = _id_list(raw["beatIds"], f"{path}.beatIds")
    return TemplateInstance(
        id=_id_value(raw["id"], f"{path}.id"),
        template_id=template_id,
        params=_parse_template_params(template_id, params_raw, f"{path}.params"),
        object_ids=object_ids,
        beat_ids=beat_ids,
    )


def _parse_template_params(
    template_id: str,
    raw: dict[str, object],
    path: str,
) -> dict[str, JsonValue]:
    if template_id == "axes_function":
        _check_keys(raw, _AXES_TEMPLATE_KEYS, path)
        _require(raw, _AXES_TEMPLATE_KEYS, path)
        return {
            "expression": _expression(raw["expression"], f"{path}.expression", PLOT_VARIABLES),
            "xRange": parse_axis_range(raw["xRange"], f"{path}.xRange"),
            "yRange": parse_axis_range(raw["yRange"], f"{path}.yRange"),
            "color": parse_color(raw["color"], f"{path}.color"),
        }
    if template_id == "formula_transform":
        _check_keys(raw, _FORMULA_TEMPLATE_KEYS, path)
        _require(raw, _FORMULA_TEMPLATE_REQUIRED, path)
        parsed: dict[str, JsonValue] = {
            "sourceLatex": parse_text(raw["sourceLatex"], f"{path}.sourceLatex", MAX_LATEX_LENGTH),
            "destinationLatex": parse_text(raw["destinationLatex"], f"{path}.destinationLatex", MAX_LATEX_LENGTH),
            "sourceColor": parse_color(raw["sourceColor"], f"{path}.sourceColor"),
            "destinationColor": parse_color(raw["destinationColor"], f"{path}.destinationColor"),
            "anchor": parse_anchor(raw["anchor"], f"{path}.anchor"),
            "shift": _shift(raw["shift"], f"{path}.shift"),
        }
        if "sourceAnchor" in raw:
            parsed["sourceAnchor"] = parse_anchor(raw["sourceAnchor"], f"{path}.sourceAnchor")
        if "destinationAnchor" in raw:
            parsed["destinationAnchor"] = parse_anchor(raw["destinationAnchor"], f"{path}.destinationAnchor")
        if "sourceShift" in raw:
            parsed["sourceShift"] = _shift(raw["sourceShift"], f"{path}.sourceShift")
        if "destinationShift" in raw:
            parsed["destinationShift"] = _shift(raw["destinationShift"], f"{path}.destinationShift")
        return parsed
    if template_id == "three_d_axes":
        _check_keys(raw, _THREE_D_TEMPLATE_KEYS, path)
        _require(raw, _THREE_D_TEMPLATE_KEYS, path)
        mode = raw["mode"]
        if not isinstance(mode, str) or mode not in {"surface", "curve"}:
            raise DocumentError(f"{path}.mode {_shown(mode)} is not a 3D mode", field=f"{path}.mode")
        return {
            "mode": mode,
            "xRange": parse_axis_range(raw["xRange"], f"{path}.xRange"),
            "yRange": parse_axis_range(raw["yRange"], f"{path}.yRange"),
            "zRange": parse_axis_range(raw["zRange"], f"{path}.zRange"),
            "phi": parse_number(raw["phi"], f"{path}.phi"),
            "theta": parse_number(raw["theta"], f"{path}.theta"),
            "zoom": _positive(raw["zoom"], f"{path}.zoom"),
            "expression": _expression(raw["expression"], f"{path}.expression", SURFACE_VARIABLES),
            "uRange": _pair_range(raw["uRange"], f"{path}.uRange"),
            "vRange": _pair_range(raw["vRange"], f"{path}.vRange"),
            "resolution": parse_resolution(raw["resolution"], f"{path}.resolution"),
            "surfaceColor": parse_color(raw["surfaceColor"], f"{path}.surfaceColor"),
            "components": _components(raw["components"], f"{path}.components"),
            "tRange": _curve_range(raw["tRange"], f"{path}.tRange"),
            "curveColor": parse_color(raw["curveColor"], f"{path}.curveColor"),
        }
    raise DocumentError(f'unknown template "{template_id}"')


def _parse_preview(raw: object) -> Profile:
    if not isinstance(raw, dict):
        raise DocumentError("previewProfile must be an object", field="previewProfile")
    _check_keys(raw, _PREVIEW_KEYS, "previewProfile")
    _require(raw, _PREVIEW_KEYS, "previewProfile")
    profile = Profile(
        width=_int_value(raw["width"], "previewProfile.width"),
        height=_int_value(raw["height"], "previewProfile.height"),
        fps=_int_value(raw["fps"], "previewProfile.fps"),
        transparent=_bool(raw["transparent"], "previewProfile.transparent"),
    )
    if (
        profile.width != PREVIEW_WIDTH
        or profile.height != PREVIEW_HEIGHT
        or profile.fps != PREVIEW_FPS
        or profile.transparent
    ):
        raise DocumentError("previewProfile must be 854×480 at 15 fps, opaque", field="previewProfile")
    return profile


def _parse_export(raw: object) -> ExportProfile:
    if not isinstance(raw, dict):
        raise DocumentError("exportProfile must be an object", field="exportProfile")
    _check_keys(raw, _EXPORT_KEYS, "exportProfile")
    _require(raw, _EXPORT_KEYS, "exportProfile")
    fmt = raw["format"]
    if not isinstance(fmt, str) or fmt not in EXPORT_FORMATS:
        raise DocumentError(f"exportProfile.format {_shown(fmt)} is not an export format", field="exportProfile.format")
    width = _int_value(raw["width"], "exportProfile.width")
    height = _int_value(raw["height"], "exportProfile.height")
    fps = _int_value(raw["fps"], "exportProfile.fps")
    transparent = _bool(raw["transparent"], "exportProfile.transparent")
    if (width, height) not in EXPORT_SIZES:
        raise DocumentError(f"exportProfile {width}×{height} is not an export size", field="exportProfile.width")
    if fps not in EXPORT_FPS:
        raise DocumentError(f"exportProfile.fps {fps} is not an export frame rate", field="exportProfile.fps")
    if fmt in {"mp4", "gif"} and transparent:
        reason = "MP4 cannot store transparency." if fmt == "mp4" else "GIF cannot store an alpha channel."
        raise DocumentError(reason, field="exportProfile.transparent")
    return ExportProfile(fmt, width, height, fps, transparent)


def _rewrite_templates(project: Project) -> None:
    """Template params win over a hand edit of the objects they own.

    A link that cannot be followed is refused, naming the template's JSON
    path, before that template rewrites anything.
    """
    for index, instance in enumerate(project.scene.template_instances):
        path = f"scene.templateInstances[{index}]"
        if instance.template_id == "axes_function":
            _sync_axes_function(project, instance, path)
        elif instance.template_id == "formula_transform":
            _sync_formula_transform(project, instance, path)
        elif instance.template_id == "three_d_axes":
            _check_three_d_axes(project, instance, path)
            _sync_three_d(project, instance)
        else:
            raise DocumentError(f'unknown template "{instance.template_id}"')


def _check_identity(project: Project) -> None:
    scene = project.scene
    ids = [("id", project.id), ("scene.id", scene.id)]
    ids.extend((f"scene.objects[{index}].id", item.id) for index, item in enumerate(scene.objects))
    ids.extend((f"scene.beats[{index}].id", item.id) for index, item in enumerate(scene.beats))
    ids.extend((f"scene.templateInstances[{index}].id", item.id) for index, item in enumerate(scene.template_instances))
    seen: set[str] = set()
    for path, value in ids:
        if value in seen:
            raise DocumentError(f'{path} repeats the id "{value}"', field=path)
        seen.add(value)


def _check_limits(project: Project) -> None:
    if total_duration(project) > MAX_TOTAL_SECONDS + 1e-9:
        raise DocumentError(
            f"scene.beats last more than {MAX_TOTAL_SECONDS:g} seconds in total",
            code="limit_exceeded",
            field="scene.beats",
        )


def _check_timeline(project: Project) -> None:
    objects = {item.id: item for item in project.scene.objects}
    for index, item in enumerate(project.scene.objects):
        path = f"scene.objects[{index}]"
        if item.type == "function_plot":
            _check_sampling(item, _require_axes(objects, item, "axes", path), path)
        elif item.type in {"surface", "parametric_curve"}:
            _require_axes(objects, item, "three_d_axes", path)
    entered: dict[str, int] = {}
    for index, beat in enumerate(project.scene.beats):
        path = f"scene.beats[{index}]"
        targets = f"{path}.targets"
        if beat.action == "wait":
            if beat.targets:
                raise DocumentError(f"{targets} must be empty for wait", field=targets)
            continue
        if beat.action == "transform_formula":
            if len(beat.targets) != 2:
                raise DocumentError(f"{targets} must be a source and a destination", field=targets)
            source_id, dest_id = beat.targets
            source = objects.get(source_id)
            dest = objects.get(dest_id)
            if source is None or dest is None:
                missing = source_id if source is None else dest_id
                raise DocumentError(f"{targets} names {missing}, which is not an object", field=targets)
            if source.type != "math_formula" or dest.type != "math_formula":
                raise DocumentError(f"{path} transforms something other than a formula", field=targets)
            if source_id not in entered:
                raise DocumentError(f"{path} transforms {source_id} before it is on screen", field=targets)
            if dest_id in entered:
                raise DocumentError(f"{path} brings {dest_id} on screen a second time", field=targets)
            entered[dest_id] = index
            continue
        if len(beat.targets) != 1:
            raise DocumentError(f"{targets} must name one object for {beat.action}", field=targets)
        target_id = beat.targets[0]
        target = objects.get(target_id)
        if target is None:
            raise DocumentError(f"{targets} names {target_id}, which is not an object", field=targets)
        if beat.action in ENTRANCES:
            if target_id in entered:
                raise DocumentError(f"{path} brings {target_id} on screen a second time", field=targets)
            if beat.action == "write" and target.type not in WRITE_TYPES:
                raise DocumentError(f"{path} writes {target_id}, but only formulas and text are written", field=targets)
            if beat.action == "create" and target.type not in CREATE_TYPES:
                raise DocumentError(f"{path} creates {target_id}, but create is not its entrance", field=targets)
            axes_id = target.params.get("axesId")
            if target.type in {"function_plot", "surface", "parametric_curve"} and axes_id not in entered:
                raise DocumentError(f"{path} brings {target_id} on screen before its axes {axes_id}", field=targets)
            entered[target_id] = index
            continue
        if target_id not in entered:
            raise DocumentError(f"{path} changes {target_id} before it is on screen", field=targets)
    for index, item in enumerate(project.scene.objects):
        if item.id not in entered:
            path = f"scene.objects[{index}]"
            raise DocumentError(f"{path} {item.id} has no entrance beat", field=path)


def _check_sampling(plot: SceneObject, axes: SceneObject, path: str) -> None:
    """Manim samples a plot ten times per x step of its axes, so the domain is capped like the ticks (D04)."""
    x_step = float(quantize(_as_number(_copy_numbers(axes.params["xRange"])[2], "xRange")))
    x_min = float(_as_number(plot.params["xMin"], "xMin"))
    x_max = float(_as_number(plot.params["xMax"], "xMax"))
    if (x_max - x_min) / x_step >= MAX_TICKS_PER_AXIS:
        raise DocumentError(
            f"{path} must span fewer than {MAX_TICKS_PER_AXIS} x steps of {axes.id} from xMin to xMax",
            code="limit_exceeded",
            field=f"{path}.params",
        )


def _check_scene_kind(project: Project) -> None:
    kind = project.scene.kind
    objects = project.scene.objects
    templates = project.scene.template_instances
    if kind == "scene":
        if project.scene.camera is not None:
            raise DocumentError("scene.camera is only for a three_d_scene", field="scene.camera")
        for index, item in enumerate(objects):
            if item.type in THREE_D_OBJECT_TYPES:
                path = f"scene.objects[{index}].type"
                raise DocumentError(f'{path} "{item.type}" belongs in a 3D scene', field=path)
        for index, instance in enumerate(templates):
            if instance.template_id == "three_d_axes":
                path = f"scene.templateInstances[{index}]"
                raise DocumentError(f"{path} is a 3D template, which needs an empty project", field=path)
        return
    if kind != "three_d_scene":
        raise DocumentError(f"scene.kind {_shown(kind)} is not a scene kind", field="scene.kind")
    if project.scene.camera is None:
        raise DocumentError("scene.camera is missing", field="scene.camera")
    for index, item in enumerate(objects):
        if item.type not in THREE_D_OBJECT_TYPES:
            path = f"scene.objects[{index}]"
            raise DocumentError(f"{path} {item.id} is in a 3D scene, which only holds the 3D template", field=path)
    if len(templates) != 1 or templates[0].template_id != "three_d_axes":
        raise DocumentError(
            "scene.templateInstances must hold the 3D template and nothing else", field="scene.templateInstances"
        )
    if len(objects) != 2:
        raise DocumentError("scene.objects must hold the 3D template's two objects only", field="scene.objects")


def _check_templates(project: Project) -> None:
    objects = {item.id: item for item in project.scene.objects}
    beats = {item.id: item for item in project.scene.beats}
    for index, instance in enumerate(project.scene.template_instances):
        path = f"scene.templateInstances[{index}]"
        if instance.template_id == "axes_function":
            _check_axes_template(instance, objects, beats, path)
        elif instance.template_id == "formula_transform":
            _check_formula_template(instance, objects, beats, path)
        elif instance.template_id == "three_d_axes":
            _check_three_d_template(project, instance, objects, beats, path)
        else:
            raise DocumentError(f'unknown template "{instance.template_id}"')


def _linked(
    instance: TemplateInstance, objects: dict[str, SceneObject], beats: dict[str, Beat], path: str
) -> tuple[SceneObject, SceneObject, Beat, Beat]:
    first_object, second_object = _owned(instance, objects, path)
    field = f"{path}.beatIds"
    if len(instance.beat_ids) != 2:
        raise DocumentError(f"{field} must name two beats", field=field)
    first_beat = beats.get(instance.beat_ids[0])
    second_beat = beats.get(instance.beat_ids[1])
    if first_beat is None or second_beat is None:
        missing = instance.beat_ids[0] if first_beat is None else instance.beat_ids[1]
        raise DocumentError(f"{field} names missing beat {missing}", field=field)
    return first_object, second_object, first_beat, second_beat


def _owned(instance: TemplateInstance, objects: dict[str, SceneObject], path: str) -> tuple[SceneObject, SceneObject]:
    """The two objects that ``objectIds`` of the template at ``path`` names."""
    field = f"{path}.objectIds"
    if len(instance.object_ids) != 2:
        raise DocumentError(f"{field} must name two objects", field=field)
    first = objects.get(instance.object_ids[0])
    second = objects.get(instance.object_ids[1])
    if first is None or second is None:
        missing = instance.object_ids[0] if first is None else instance.object_ids[1]
        raise DocumentError(f"{field} names missing object {missing}", field=field)
    return first, second


def _check_axes_template(
    instance: TemplateInstance,
    objects: dict[str, SceneObject],
    beats: dict[str, Beat],
    path: str,
) -> None:
    axes, plot, first, second = _linked(instance, objects, beats, path)
    if axes.type != "axes" or plot.type != "function_plot":
        raise DocumentError(f"{path} must own an axes and a function plot", field=f"{path}.objectIds")
    if plot.params.get("axesId") != axes.id:
        raise DocumentError(f"{path} plot {plot.id} must use the axes {axes.id}", field=f"{path}.objectIds")
    if first.action != "create" or first.targets != [axes.id]:
        raise DocumentError(f"{path} first beat must create the axes", field=f"{path}.beatIds")
    if second.action != "create" or second.targets != [plot.id]:
        raise DocumentError(f"{path} second beat must create the plot", field=f"{path}.beatIds")


def _check_formula_template(
    instance: TemplateInstance,
    objects: dict[str, SceneObject],
    beats: dict[str, Beat],
    path: str,
) -> None:
    source, dest, first, second = _linked(instance, objects, beats, path)
    if source.type != "math_formula" or dest.type != "math_formula":
        raise DocumentError(f"{path} must own two formulas", field=f"{path}.objectIds")
    if first.action != "write" or first.targets != [source.id]:
        raise DocumentError(f"{path} first beat must write the source formula", field=f"{path}.beatIds")
    if second.action != "transform_formula" or second.targets != [source.id, dest.id]:
        raise DocumentError(f"{path} second beat must be the transform", field=f"{path}.beatIds")


def _check_three_d_template(
    project: Project,
    instance: TemplateInstance,
    objects: dict[str, SceneObject],
    beats: dict[str, Beat],
    path: str,
) -> None:
    axes, child, first, second = _linked(instance, objects, beats, path)
    mode = instance.params.get("mode")
    expected = "surface" if mode == "surface" else "parametric_curve"
    if axes.type != "three_d_axes" or child.type != expected:
        raise DocumentError(f"{path} must own the 3D axes and one surface or curve", field=f"{path}.objectIds")
    if child.params.get("axesId") != axes.id:
        raise DocumentError(f"{path} {child.id} must use the axes {axes.id}", field=f"{path}.objectIds")
    if first.action != "create" or first.targets != [axes.id]:
        raise DocumentError(f"{path} first beat must create the axes", field=f"{path}.beatIds")
    if second.action != "create" or second.targets != [child.id]:
        raise DocumentError(f"{path} second beat must create the surface or curve", field=f"{path}.beatIds")
    camera = project.scene.camera
    if camera is None:
        raise DocumentError("scene.camera is missing", field="scene.camera")
    if (
        camera.phi != instance.params["phi"]
        or camera.theta != instance.params["theta"]
        or camera.zoom != instance.params["zoom"]
    ):
        raise DocumentError(f"scene.camera must match {path}.params", field="scene.camera")


def _sync_axes_function(project: Project, instance: TemplateInstance, path: str) -> None:
    axes, plot = _owned(instance, {item.id: item for item in project.scene.objects}, path)
    if axes.type != "axes" or plot.type != "function_plot":
        raise DocumentError(f"{path} must own an axes and a function plot", field=f"{path}.objectIds")
    axes.params["xRange"] = _copy_numbers(instance.params["xRange"])
    axes.params["yRange"] = _copy_numbers(instance.params["yRange"])
    plot.params["expression"] = instance.params["expression"]
    plot.params["color"] = instance.params["color"]


def _sync_formula_transform(project: Project, instance: TemplateInstance, path: str) -> None:
    source, dest = _owned(instance, {item.id: item for item in project.scene.objects}, path)
    if source.type != "math_formula" or dest.type != "math_formula":
        raise DocumentError(f"{path} must own two formulas", field=f"{path}.objectIds")
    source.params["latex"] = instance.params["sourceLatex"]
    source.params["color"] = instance.params["sourceColor"]
    source.params["anchor"] = _formula_side(instance.params, "source", "anchor")
    source.params["shift"] = _formula_side(instance.params, "source", "shift")
    dest.params["latex"] = instance.params["destinationLatex"]
    dest.params["color"] = instance.params["destinationColor"]
    dest.params["anchor"] = _formula_side(instance.params, "destination", "anchor")
    dest.params["shift"] = _formula_side(instance.params, "destination", "shift")


def _check_three_d_axes(project: Project, instance: TemplateInstance, path: str) -> None:
    """The 3D template names its axes first. A missing or retyped second object is replaced by the sync."""
    field = f"{path}.objectIds"
    if len(instance.object_ids) != 2:
        raise DocumentError(f"{field} must name two objects", field=field)
    axes_id = instance.object_ids[0]
    if not any(item.id == axes_id and item.type == "three_d_axes" for item in project.scene.objects):
        raise DocumentError(f"{field} names {axes_id}, which is not 3D axes", field=field)


def _sync_three_d(project: Project, instance: TemplateInstance) -> None:
    """Rewrite the 3D axes, the surface or curve, and the camera from the template.

    Load checks the axes link first (``_check_three_d_axes``), so an edit finds it in place.
    """
    expected = "surface" if instance.params["mode"] == "surface" else "parametric_curve"
    objects = {item.id: item for item in project.scene.objects}
    axes = objects[instance.object_ids[0]]
    axes.params["xRange"] = _copy_numbers(instance.params["xRange"])
    axes.params["yRange"] = _copy_numbers(instance.params["yRange"])
    axes.params["zRange"] = _copy_numbers(instance.params["zRange"])
    child = objects.get(instance.object_ids[1])
    if child is None or child.type != expected:
        child = _swap_three_d_child(project, instance, expected)
    child.params["axesId"] = axes.id
    if expected == "surface":
        child.params["expression"] = instance.params["expression"]
        child.params["uRange"] = _copy_numbers(instance.params["uRange"])
        child.params["vRange"] = _copy_numbers(instance.params["vRange"])
        child.params["resolution"] = _copy_numbers(instance.params["resolution"])
        child.params["color"] = instance.params["surfaceColor"]
    else:
        components = instance.params["components"]
        if not isinstance(components, list):
            raise DocumentError("curve components must be three expressions")
        child.params["components"] = list(components)
        child.params["tRange"] = _copy_numbers(instance.params["tRange"])
        child.params["color"] = instance.params["curveColor"]
    project.scene.kind = "three_d_scene"
    project.scene.camera = Camera(
        phi=_as_number(instance.params["phi"], "phi"),
        theta=_as_number(instance.params["theta"], "theta"),
        zoom=_as_number(instance.params["zoom"], "zoom"),
    )


def _swap_three_d_child(project: Project, instance: TemplateInstance, expected: str) -> SceneObject:
    """Replace the second object and retarget the second beat. Beat ids stay."""
    old_id = instance.object_ids[1]
    index = 1
    for position, item in enumerate(project.scene.objects):
        if item.id == old_id:
            index = position
            break
    project.scene.objects = [item for item in project.scene.objects if item.id != old_id]
    used = used_ids(project)
    axes_id = instance.object_ids[0]
    if expected == "surface":
        child = SceneObject(
            id=fresh_id("surface", used),
            name="Surface",
            type="surface",
            params={
                "axesId": axes_id,
                "expression": instance.params["expression"],
                "uRange": _copy_numbers(instance.params["uRange"]),
                "vRange": _copy_numbers(instance.params["vRange"]),
                "resolution": _copy_numbers(instance.params["resolution"]),
                "color": instance.params["surfaceColor"],
            },
        )
    elif expected == "parametric_curve":
        components = instance.params["components"]
        if not isinstance(components, list):
            raise DocumentError("curve components must be three expressions")
        child = SceneObject(
            id=fresh_id("curve", used),
            name="Curve",
            type="parametric_curve",
            params={
                "axesId": axes_id,
                "components": list(components),
                "tRange": _copy_numbers(instance.params["tRange"]),
                "color": instance.params["curveColor"],
            },
        )
    else:
        raise DocumentError(f"unknown 3D object {expected}")
    project.scene.objects.insert(index, child)
    instance.object_ids[1] = child.id
    for beat in project.scene.beats:
        if old_id in beat.targets:
            beat.targets = [child.id if target == old_id else target for target in beat.targets]
    return child


def _require_axes(objects: dict[str, SceneObject], item: SceneObject, axes_type: str, path: str) -> SceneObject:
    axes_id = item.params.get("axesId")
    axes = objects.get(axes_id) if isinstance(axes_id, str) else None
    if axes is None or axes.type != axes_type:
        field = f"{path}.params.axesId"
        raise DocumentError(f"{field} must name a {axes_type} object", field=field)
    return axes


def _object_dict(item: SceneObject) -> dict[str, JsonValue]:
    order = _PARAM_ORDER[item.type]
    params = {key: item.params[key] for key in order if key in item.params}
    return {"id": item.id, "name": item.name, "type": item.type, "params": params}


def _beat_dict(item: Beat) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": item.id,
        "duration": item.duration,
        "action": item.action,
        "targets": list(item.targets),
    }
    if item.params is not None:
        if item.action == "set_color":
            payload["params"] = {"color": item.params["color"]}
        elif item.action == "move":
            payload["params"] = {"shift": item.params["shift"]}
    return payload


def _template_dict(item: TemplateInstance) -> dict[str, JsonValue]:
    order = _TEMPLATE_ORDERS[item.template_id]
    params = {key: item.params[key] for key in order if key in item.params}
    return {
        "id": item.id,
        "templateId": item.template_id,
        "params": params,
        "objectIds": list(item.object_ids),
        "beatIds": list(item.beat_ids),
    }


def _scene_without_names(project: Project) -> dict[str, JsonValue]:
    scene = project_dict(project)["scene"]
    if not isinstance(scene, dict):
        raise TypeError("scene dict missing")
    scene.pop("name", None)
    objects = scene.get("objects")
    if isinstance(objects, list):
        for item in objects:
            if isinstance(item, dict):
                item.pop("name", None)
    return scene


def _beat_cache_payload(project: Project, index: int, profile: str) -> dict[str, JsonValue]:
    beat = project.scene.beats[index]
    profile_payload: dict[str, JsonValue]
    if profile == "preview":
        chosen = project.preview_profile
        profile_payload = {
            "fps": chosen.fps,
            "height": chosen.height,
            "transparent": chosen.transparent,
            "width": chosen.width,
        }
    elif profile == "export":
        chosen_export = project.export_profile
        profile_payload = {
            "format": chosen_export.format,
            "fps": chosen_export.fps,
            "height": chosen_export.height,
            "transparent": chosen_export.transparent,
            "width": chosen_export.width,
        }
    else:
        raise DocumentError(f"unknown profile {profile}")
    beat_payload: dict[str, JsonValue] = {
        "action": beat.action,
        "duration": beat.duration,
        "id": beat.id,
        "targets": list(beat.targets),
    }
    if beat.params is not None:
        beat_payload["params"] = dict(beat.params)
    return {
        "beat": beat_payload,
        "beatIndex": index,
        "generatorVersion": project.generator_version,
        "manimVersion": MANIM_VERSION,
        "objects": _objects_for_beat(project, beat),
        "profile": profile_payload,
        "seed": project.seed,
    }


def _objects_for_beat(project: Project, beat: Beat) -> list[JsonValue]:
    if beat.action == "wait":
        return []
    objects = {item.id: item for item in project.scene.objects}
    ordered: list[JsonValue] = []
    seen: set[str] = set()

    def add(object_id: str) -> None:
        if object_id in seen:
            return
        item = objects[object_id]
        if item.type in {"function_plot", "surface", "parametric_curve"}:
            axes_id = item.params.get("axesId")
            if isinstance(axes_id, str):
                add(axes_id)
        seen.add(object_id)
        ordered.append(_object_without_name(item))

    for target in beat.targets:
        add(target)
    return ordered


def _object_without_name(item: SceneObject) -> dict[str, JsonValue]:
    payload = _object_dict(item)
    payload.pop("name", None)
    return payload


def _invalid_json(detail: str) -> DocumentError:
    return DocumentError(f"project.json is not valid JSON: {detail}.", code="invalid_json")


def _finite_float(literal: str) -> float:
    number = float(literal)
    if math.isinf(number):
        raise ValueError("a number is too large")
    return number


def _finite_int(literal: str) -> int:
    """An integer literal that a float can hold, which also keeps int() under its digit limit."""
    _finite_float(literal)
    return int(literal)


def _not_a_number(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a JSON number")


def _shown(value: object) -> str:
    """``value`` as JSON for a message. An array or an object is named, not printed."""
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return json.dumps(value)


def _check_keys(raw: dict[str, object], allowed: set[str], path: str) -> None:
    """Refuse unknown keys. ``path`` is "" for the document root.

    Every object the parser accepts passes through here, so this is also the
    one place that refuses a ``start`` field anywhere in the file.
    """
    for key in raw:
        field = f"{path}.{key}" if path else key
        if key == "start":
            raise DocumentError(f"{field} is not allowed: beat starts are derived from durations", field=field)
        if key not in allowed:
            raise DocumentError(f"{field} is not a known key", field=field)


def _require(raw: dict[str, object], keys: set[str], path: str) -> None:
    for key in sorted(keys):
        if key not in raw:
            field = f"{path}.{key}" if path else key
            raise DocumentError(f"{field} is missing", field=field)


def _formula_side(params: dict[str, JsonValue], side: str, field_name: str) -> JsonValue:
    per_side = f"{side}{field_name[:1].upper()}{field_name[1:]}"
    if per_side in params:
        value = params[per_side]
        if field_name == "shift" and isinstance(value, list):
            return list(value)
        return value
    shared = params[field_name]
    if field_name == "shift" and isinstance(shared, list):
        return list(shared)
    return shared


def _reject_generated_name(value: str, path: str) -> None:
    if keyword.iskeyword(value) or value in _GENERATED_NAMES:
        raise DocumentError(f"{path} {value!r} is not a valid generated name", field=path)


def _id_value(value: object, path: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise DocumentError(f"{path} {_shown(value)} is not a valid id", field=path)
    return value


def _id_list(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        raise DocumentError(f"{path} must be an array", field=path)
    return [_id_value(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _is_int(value: object) -> TypeIs[int]:
    """A JSON integer: ``true`` and ``1.0`` are not integers (F014)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _int_value(value: object, path: str) -> int:
    if not _is_int(value):
        raise DocumentError(f"{path} must be an integer", field=path)
    return value


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise DocumentError(f"{path} must be a boolean", field=path)
    return value


def _numbers(value: object, path: str, count: int) -> list[Number]:
    if not isinstance(value, list) or len(value) != count:
        raise DocumentError(f"{path} must be {count} numbers", field=path)
    return [parse_number(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _check_greater(high: Number, low: Number, field: str, than: str, part: str = "") -> None:
    """``high > low``, also at the three decimals codegen emits (D02)."""
    subject = f"{field} {part}" if part else field
    if float(high) <= float(low):
        raise DocumentError(f"{subject} must be greater than {than}", field=field)
    if quantize(high) <= quantize(low):
        raise DocumentError(f"{subject} rounds to {than} at three decimal places", field=field)


def _positive(value: object, path: str) -> Number:
    number = parse_number(value, path)
    _check_greater(number, 0, path, "0")
    return number


def _duration(value: object, path: str) -> Number:
    number = parse_number(value, path)
    if float(number) < MIN_BEAT_SECONDS:
        raise DocumentError(f"{path} must be at least 0.1 seconds", code="limit_exceeded", field=path)
    return number


def _shift(value: object, path: str) -> list[JsonValue]:
    return list[JsonValue](_numbers(value, path, 2))


def parse_resolution(value: object, path: str) -> list[JsonValue]:
    """Two integers from 1 to 24. A larger value is refused and nothing is stored."""
    if not isinstance(value, list) or len(value) != 2:
        raise DocumentError(f"{path} must be two integers", field=path)
    numbers: list[JsonValue] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise DocumentError(f"{path} must be two integers", field=path)
        if isinstance(item, float) and not item.is_integer():
            raise DocumentError(f"{path} must be two integers", field=path)
        number = int(item)
        if number > MAX_RESOLUTION:
            raise DocumentError(f"{path} is above the 24×24 resolution cap.", code="limit_exceeded", field=path)
        if number < 1:
            raise DocumentError(f"{path} must be from 1 to 24", field=path)
        numbers.append(number)
    return numbers


def _expression(value: object, path: str, variables: frozenset[str]) -> str:
    text = parse_text(value, path)
    assert_supported(text, variables, path)
    return text


def _pair_range(value: object, path: str) -> list[JsonValue]:
    numbers = _numbers(value, path, 2)
    _check_greater(numbers[1], numbers[0], path, "min", "max")
    return list[JsonValue](numbers)


def _curve_range(value: object, path: str) -> list[JsonValue]:
    """``[tMin, tMax]`` of a curve, which spans less than 50 to bound its sampling (D04)."""
    numbers = _numbers(value, path, 2)
    low, high = numbers
    _check_greater(high, low, path, "min", "max")
    if float(quantize(high)) - float(quantize(low)) >= MAX_CURVE_SPAN:
        raise DocumentError(f"{path} must span less than {MAX_CURVE_SPAN}", code="limit_exceeded", field=path)
    return list[JsonValue](numbers)


def _components(value: object, path: str) -> list[JsonValue]:
    if not isinstance(value, list) or len(value) != 3:
        raise DocumentError(f"{path} must be three expressions", field=path)
    return [_expression(item, f"{path}[{index}]", CURVE_VARIABLES) for index, item in enumerate(value)]


def _parse_camera(value: object, path: str) -> Camera:
    if not isinstance(value, dict):
        raise DocumentError(f"{path} must be an object", field=path)
    _check_keys(value, _CAMERA_KEYS, path)
    _require(value, _CAMERA_KEYS, path)
    return Camera(
        phi=parse_number(value["phi"], f"{path}.phi"),
        theta=parse_number(value["theta"], f"{path}.theta"),
        zoom=_positive(value["zoom"], f"{path}.zoom"),
    )


def _copy_numbers(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise DocumentError("expected a list of numbers")
    return list(value)


def _as_number(value: JsonValue, path: str) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError(f"{path} must be a number")
    return value


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
