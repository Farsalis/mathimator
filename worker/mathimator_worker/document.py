"""project.json model, validation, and cache identity.

``schemaVersion`` 1 is the only version this phase reads. A ``start`` field,
an unknown key, or any other schema version is refused with a message that
names the problem. Beat start times are derived and are not stored.
"""

from __future__ import annotations

import hashlib
import json
import keyword
import math
import re
from dataclasses import dataclass, field

from mathimator_worker.document_error import DocumentError
from mathimator_worker.expr import assert_supported
from mathimator_worker.jsonio import JsonValue, canonical_dumps, dumps_document

MANIM_VERSION = "0.22.0"
SCHEMA_VERSION = 1
GENERATOR_VERSION = 1
MIN_BEAT_SECONDS = 0.1
MAX_TOTAL_SECONDS = 120.0

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


def load_text(text: str) -> Project:
    """Parse ``text`` and return a project, or raise DocumentError."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentError(f"project.json is not valid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise DocumentError("project.json must be a JSON object")
    if "schemaVersion" not in raw:
        raise DocumentError("schemaVersion is missing")
    version = raw["schemaVersion"]
    if version != SCHEMA_VERSION:
        raise DocumentError(f"schemaVersion {version!r} is not supported")
    _reject_start(raw)
    project = _parse_project(raw)
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


def object_by_id(project: Project, object_id: str) -> SceneObject:
    for item in project.scene.objects:
        if item.id == object_id:
            return item
    raise DocumentError(f"unknown object {object_id}")


def beat_by_id(project: Project, beat_id: str) -> Beat:
    for beat in project.scene.beats:
        if beat.id == beat_id:
            return beat
    raise DocumentError(f"unknown beat {beat_id}")


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


def normalize_color(value: str) -> str:
    if not isinstance(value, str) or _COLOR.fullmatch(value) is None:
        raise DocumentError("color must be #RRGGBB")
    return value.upper()


def _parse_project(raw: dict[str, object]) -> Project:
    _check_keys(raw, _PROJECT_KEYS, "project")
    _require(raw, _PROJECT_KEYS, "project")
    generator = raw["generatorVersion"]
    if generator != GENERATOR_VERSION:
        raise DocumentError(f"generatorVersion {generator!r} is not supported")
    scene_raw = raw["scene"]
    if not isinstance(scene_raw, dict):
        raise DocumentError("scene must be an object")
    return Project(
        schema_version=SCHEMA_VERSION,
        generator_version=GENERATOR_VERSION,
        id=_id_value(raw["id"], "id"),
        name=_string(raw["name"], "name"),
        seed=_int_value(raw["seed"], "seed"),
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
    if kind not in {"scene", "three_d_scene"}:
        raise DocumentError(f"unknown scene kind {kind!r}")
    if kind == "three_d_scene" and "camera" not in raw:
        raise DocumentError('missing key "camera" in scene')
    objects_raw = raw["objects"]
    beats_raw = raw["beats"]
    templates_raw = raw["templateInstances"]
    if not isinstance(objects_raw, list):
        raise DocumentError("scene.objects must be an array")
    if not isinstance(beats_raw, list):
        raise DocumentError("scene.beats must be an array")
    if not isinstance(templates_raw, list):
        raise DocumentError("scene.templateInstances must be an array")
    camera = None
    if kind == "three_d_scene":
        camera = _parse_camera(raw["camera"], "scene.camera")
    return Scene(
        id=_id_value(raw["id"], "scene.id"),
        name=_string(raw["name"], "scene.name"),
        kind=kind,
        background=_color(raw["background"], "scene.background"),
        objects=[_parse_object(item, index) for index, item in enumerate(objects_raw)],
        beats=[_parse_beat(item, index) for index, item in enumerate(beats_raw)],
        template_instances=[_parse_template(item, index) for index, item in enumerate(templates_raw)],
        camera=camera,
    )


def _parse_object(raw: object, index: int) -> SceneObject:
    path = f"scene.objects[{index}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object")
    _check_keys(raw, _OBJECT_KEYS, path)
    _require(raw, _OBJECT_KEYS, path)
    object_type = raw["type"]
    if not isinstance(object_type, str):
        raise DocumentError(f"{path}.type must be a string")
    if object_type not in OBJECT_TYPES:
        raise DocumentError(f'unknown object type "{object_type}"')
    params = raw["params"]
    if not isinstance(params, dict):
        raise DocumentError(f"{path}.params must be an object")
    object_id = _id_value(raw["id"], f"{path}.id")
    _reject_generated_name(object_id)
    return SceneObject(
        id=object_id,
        name=_string(raw["name"], f"{path}.name"),
        type=object_type,
        params=_parse_params(object_type, params, f"{path}.params"),
    )


def _parse_params(object_type: str, raw: dict[str, object], path: str) -> dict[str, JsonValue]:
    if object_type == "axes":
        _check_keys(raw, _AXES_KEYS, path)
        _require(raw, _AXES_KEYS, path)
        return {
            "xRange": _range3(raw["xRange"], f"{path}.xRange"),
            "yRange": _range3(raw["yRange"], f"{path}.yRange"),
            "xLength": _positive(raw["xLength"], f"{path}.xLength"),
            "yLength": _positive(raw["yLength"], f"{path}.yLength"),
            "color": _color(raw["color"], f"{path}.color"),
            "showLabels": _bool(raw["showLabels"], f"{path}.showLabels"),
        }
    if object_type == "function_plot":
        _check_keys(raw, _PLOT_KEYS, path)
        _require(raw, _PLOT_KEYS, path)
        expression = _expression(raw["expression"], f"{path}.expression", PLOT_VARIABLES)
        return {
            "axesId": _id_value(raw["axesId"], f"{path}.axesId"),
            "expression": expression,
            "xMin": _number(raw["xMin"], f"{path}.xMin"),
            "xMax": _number(raw["xMax"], f"{path}.xMax"),
            "color": _color(raw["color"], f"{path}.color"),
        }
    if object_type == "math_formula":
        _check_keys(raw, _FORMULA_KEYS, path)
        _require(raw, {"latex", "color", "anchor"}, path)
        latex = _string(raw["latex"], f"{path}.latex")
        if len(latex) > 500:
            raise DocumentError(f"{path}.latex is longer than 500 characters")
        formula_params: dict[str, JsonValue] = {
            "latex": latex,
            "color": _color(raw["color"], f"{path}.color"),
            "anchor": _anchor(raw["anchor"], f"{path}.anchor"),
        }
        if "shift" in raw:
            formula_params["shift"] = _shift(raw["shift"], f"{path}.shift")
        return formula_params
    if object_type == "text":
        _check_keys(raw, _TEXT_KEYS, path)
        _require(raw, {"text", "color", "anchor"}, path)
        text = _string(raw["text"], f"{path}.text")
        if len(text) > 200:
            raise DocumentError(f"{path}.text is longer than 200 characters")
        text_params: dict[str, JsonValue] = {
            "text": text,
            "color": _color(raw["color"], f"{path}.color"),
            "anchor": _anchor(raw["anchor"], f"{path}.anchor"),
        }
        if "shift" in raw:
            text_params["shift"] = _shift(raw["shift"], f"{path}.shift")
        return text_params
    if object_type == "three_d_axes":
        _check_keys(raw, _THREE_D_AXES_KEYS, path)
        _require(raw, {"xRange", "yRange", "zRange"}, path)
        params: dict[str, JsonValue] = {
            "xRange": _range3(raw["xRange"], f"{path}.xRange"),
            "yRange": _range3(raw["yRange"], f"{path}.yRange"),
            "zRange": _range3(raw["zRange"], f"{path}.zRange"),
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
            "color": _color(raw["color"], f"{path}.color"),
        }
    if object_type == "parametric_curve":
        _check_keys(raw, _CURVE_KEYS, path)
        _require(raw, _CURVE_KEYS, path)
        return {
            "axesId": _id_value(raw["axesId"], f"{path}.axesId"),
            "components": _components(raw["components"], f"{path}.components"),
            "tRange": _pair_range(raw["tRange"], f"{path}.tRange"),
            "color": _color(raw["color"], f"{path}.color"),
        }
    raise DocumentError(f'unknown object type "{object_type}"')


def _parse_beat(raw: object, index: int) -> Beat:
    path = f"scene.beats[{index}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object")
    _check_keys(raw, _BEAT_KEYS, path)
    _require(raw, {"id", "duration", "action", "targets"}, path)
    action = raw["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise DocumentError(f"unknown action {action!r}")
    targets_raw = raw["targets"]
    if not isinstance(targets_raw, list) or not all(isinstance(item, str) for item in targets_raw):
        raise DocumentError(f"{path}.targets must be an array of ids")
    targets = [_id_value(item, f"{path}.targets") for item in targets_raw]
    params: dict[str, JsonValue] | None = None
    if action == "set_color":
        params = _parse_color_params(raw.get("params"), f"{path}.params")
    elif action == "move":
        params = _parse_move_params(raw.get("params"), f"{path}.params")
    elif "params" in raw:
        raise DocumentError(f"{action} does not take params")
    return Beat(
        id=_id_value(raw["id"], f"{path}.id"),
        duration=_duration(raw["duration"], f"{path}.duration"),
        action=action,
        targets=targets,
        params=params,
    )


def _parse_color_params(raw: object, path: str) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object")
    _check_keys(raw, _SET_COLOR_KEYS, path)
    _require(raw, _SET_COLOR_KEYS, path)
    return {"color": _color(raw["color"], f"{path}.color")}


def _parse_move_params(raw: object, path: str) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object")
    _check_keys(raw, _MOVE_KEYS, path)
    _require(raw, _MOVE_KEYS, path)
    return {"shift": _shift(raw["shift"], f"{path}.shift")}


def _parse_template(raw: object, index: int) -> TemplateInstance:
    path = f"scene.templateInstances[{index}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{path} must be an object")
    _check_keys(raw, _TEMPLATE_KEYS, path)
    _require(raw, _TEMPLATE_KEYS, path)
    template_id = raw["templateId"]
    if not isinstance(template_id, str) or template_id not in TEMPLATE_IDS:
        raise DocumentError(f'unknown template "{template_id}"')
    params_raw = raw["params"]
    if not isinstance(params_raw, dict):
        raise DocumentError(f"{path}.params must be an object")
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
            "xRange": _range3(raw["xRange"], f"{path}.xRange"),
            "yRange": _range3(raw["yRange"], f"{path}.yRange"),
            "color": _color(raw["color"], f"{path}.color"),
        }
    if template_id == "formula_transform":
        _check_keys(raw, _FORMULA_TEMPLATE_KEYS, path)
        _require(raw, _FORMULA_TEMPLATE_REQUIRED, path)
        source = _string(raw["sourceLatex"], f"{path}.sourceLatex")
        destination = _string(raw["destinationLatex"], f"{path}.destinationLatex")
        if len(source) > 500 or len(destination) > 500:
            raise DocumentError(f"{path} latex is longer than 500 characters")
        parsed: dict[str, JsonValue] = {
            "sourceLatex": source,
            "destinationLatex": destination,
            "sourceColor": _color(raw["sourceColor"], f"{path}.sourceColor"),
            "destinationColor": _color(raw["destinationColor"], f"{path}.destinationColor"),
            "anchor": _anchor(raw["anchor"], f"{path}.anchor"),
            "shift": _shift(raw["shift"], f"{path}.shift"),
        }
        if "sourceAnchor" in raw:
            parsed["sourceAnchor"] = _anchor(raw["sourceAnchor"], f"{path}.sourceAnchor")
        if "destinationAnchor" in raw:
            parsed["destinationAnchor"] = _anchor(raw["destinationAnchor"], f"{path}.destinationAnchor")
        if "sourceShift" in raw:
            parsed["sourceShift"] = _shift(raw["sourceShift"], f"{path}.sourceShift")
        if "destinationShift" in raw:
            parsed["destinationShift"] = _shift(raw["destinationShift"], f"{path}.destinationShift")
        return parsed
    if template_id == "three_d_axes":
        _check_keys(raw, _THREE_D_TEMPLATE_KEYS, path)
        _require(raw, _THREE_D_TEMPLATE_KEYS, path)
        mode = raw["mode"]
        if mode not in {"surface", "curve"}:
            raise DocumentError(f"unknown 3D mode {mode!r}")
        return {
            "mode": mode,
            "xRange": _range3(raw["xRange"], f"{path}.xRange"),
            "yRange": _range3(raw["yRange"], f"{path}.yRange"),
            "zRange": _range3(raw["zRange"], f"{path}.zRange"),
            "phi": _number(raw["phi"], f"{path}.phi"),
            "theta": _number(raw["theta"], f"{path}.theta"),
            "zoom": _positive(raw["zoom"], f"{path}.zoom"),
            "expression": _expression(raw["expression"], f"{path}.expression", SURFACE_VARIABLES),
            "uRange": _pair_range(raw["uRange"], f"{path}.uRange"),
            "vRange": _pair_range(raw["vRange"], f"{path}.vRange"),
            "resolution": parse_resolution(raw["resolution"], f"{path}.resolution"),
            "surfaceColor": _color(raw["surfaceColor"], f"{path}.surfaceColor"),
            "components": _components(raw["components"], f"{path}.components"),
            "tRange": _pair_range(raw["tRange"], f"{path}.tRange"),
            "curveColor": _color(raw["curveColor"], f"{path}.curveColor"),
        }
    raise DocumentError(f'unknown template "{template_id}"')


def _parse_preview(raw: object) -> Profile:
    if not isinstance(raw, dict):
        raise DocumentError("previewProfile must be an object")
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
        raise DocumentError("previewProfile must be 854×480 at 15 fps, opaque")
    return profile


def _parse_export(raw: object) -> ExportProfile:
    if not isinstance(raw, dict):
        raise DocumentError("exportProfile must be an object")
    _check_keys(raw, _EXPORT_KEYS, "exportProfile")
    _require(raw, _EXPORT_KEYS, "exportProfile")
    fmt = raw["format"]
    if not isinstance(fmt, str) or fmt not in EXPORT_FORMATS:
        raise DocumentError(f"unknown export format {fmt!r}")
    width = _int_value(raw["width"], "exportProfile.width")
    height = _int_value(raw["height"], "exportProfile.height")
    fps = _int_value(raw["fps"], "exportProfile.fps")
    transparent = _bool(raw["transparent"], "exportProfile.transparent")
    if (width, height) not in EXPORT_SIZES:
        raise DocumentError(f"export resolution {width}×{height} is not supported")
    if fps not in EXPORT_FPS:
        raise DocumentError(f"export frame rate {fps} is not supported")
    if fmt in {"mp4", "gif"} and transparent:
        reason = "MP4 cannot store transparency." if fmt == "mp4" else "GIF cannot store an alpha channel."
        raise DocumentError(reason)
    return ExportProfile(fmt, width, height, fps, transparent)


def _rewrite_templates(project: Project) -> None:
    """Template params win over a hand edit of the objects they own."""
    for instance in project.scene.template_instances:
        if instance.template_id == "axes_function":
            _sync_axes_function(project, instance)
        elif instance.template_id == "formula_transform":
            _sync_formula_transform(project, instance)
        elif instance.template_id == "three_d_axes":
            _sync_three_d(project, instance)
        else:
            raise DocumentError(f'unknown template "{instance.template_id}"')


def _check_identity(project: Project) -> None:
    seen: set[str] = set()
    values = [project.id, project.scene.id]
    values.extend(item.id for item in project.scene.objects)
    values.extend(item.id for item in project.scene.beats)
    values.extend(item.id for item in project.scene.template_instances)
    for value in values:
        if value in seen:
            raise DocumentError(f'duplicate id "{value}"')
        seen.add(value)


def _check_limits(project: Project) -> None:
    for beat in project.scene.beats:
        if float(beat.duration) < MIN_BEAT_SECONDS:
            raise DocumentError("beat duration must be at least 0.1 seconds")
    if total_duration(project) > MAX_TOTAL_SECONDS + 1e-9:
        raise DocumentError("total duration must be at most 120 seconds")


def _check_timeline(project: Project) -> None:
    objects = {item.id: item for item in project.scene.objects}
    for item in project.scene.objects:
        if item.type == "function_plot":
            _require_axes(objects, item, "axes")
            x_min = float(item.params["xMin"])  # type: ignore[arg-type]
            x_max = float(item.params["xMax"])  # type: ignore[arg-type]
            if x_max <= x_min:
                raise DocumentError(f"{item.id} xMax must be greater than xMin")
        elif item.type in {"surface", "parametric_curve"}:
            _require_axes(objects, item, "three_d_axes")
    entered: dict[str, int] = {}
    for index, beat in enumerate(project.scene.beats):
        if beat.action == "wait":
            if beat.targets:
                raise DocumentError("wait cannot target an object")
            continue
        if beat.action == "transform_formula":
            if len(beat.targets) != 2:
                raise DocumentError("transform_formula needs a source and a destination")
            source_id, dest_id = beat.targets
            source = objects.get(source_id)
            dest = objects.get(dest_id)
            if source is None or dest is None:
                raise DocumentError("transform_formula points at a missing object")
            if source.type != "math_formula" or dest.type != "math_formula":
                raise DocumentError("transform_formula only transforms formulas")
            if source_id not in entered:
                raise DocumentError("transform source must already be on screen")
            if dest_id in entered:
                raise DocumentError("transform is the destination entrance")
            entered[dest_id] = index
            continue
        if len(beat.targets) != 1:
            raise DocumentError(f"{beat.action} needs one target")
        target_id = beat.targets[0]
        target = objects.get(target_id)
        if target is None:
            raise DocumentError(f"{beat.action} points at a missing object")
        if beat.action in ENTRANCES:
            if target_id in entered:
                raise DocumentError(f"{target_id} already has an entrance")
            if beat.action == "write" and target.type not in WRITE_TYPES:
                raise DocumentError("write is only for formulas and text")
            if beat.action == "create" and target.type not in CREATE_TYPES:
                raise DocumentError("create is not the entrance for this object")
            entered[target_id] = index
            continue
        if target_id not in entered:
            raise DocumentError(f"{target_id} is not on screen")
    for item in project.scene.objects:
        if item.id not in entered:
            raise DocumentError(f"{item.id} has no entrance")


def _check_scene_kind(project: Project) -> None:
    kind = project.scene.kind
    objects = project.scene.objects
    templates = project.scene.template_instances
    if kind == "scene":
        if project.scene.camera is not None:
            raise DocumentError('unknown key "camera" in scene')
        for item in objects:
            if item.type in THREE_D_OBJECT_TYPES:
                raise DocumentError(f'object type "{item.type}" belongs in a 3D scene')
        if any(item.template_id == "three_d_axes" for item in templates):
            raise DocumentError("A 3D template needs an empty project.")
        return
    if kind != "three_d_scene":
        raise DocumentError(f"unknown scene kind {kind!r}")
    if project.scene.camera is None:
        raise DocumentError("a 3D scene needs a camera")
    for item in objects:
        if item.type not in THREE_D_OBJECT_TYPES:
            raise DocumentError("A 3D scene only holds the 3D template.")
    if len(templates) != 1 or templates[0].template_id != "three_d_axes":
        raise DocumentError("A 3D scene only holds the 3D template.")
    if len(objects) != 2:
        raise DocumentError("A 3D scene only holds the 3D template.")


def _check_templates(project: Project) -> None:
    objects = {item.id: item for item in project.scene.objects}
    beats = {item.id: item for item in project.scene.beats}
    for instance in project.scene.template_instances:
        if instance.template_id == "axes_function":
            _check_axes_template(instance, objects, beats)
        elif instance.template_id == "formula_transform":
            _check_formula_template(instance, objects, beats)
        elif instance.template_id == "three_d_axes":
            _check_three_d_template(project, instance, objects, beats)
        else:
            raise DocumentError(f'unknown template "{instance.template_id}"')


def _linked(
    instance: TemplateInstance, objects: dict[str, SceneObject], beats: dict[str, Beat]
) -> tuple[SceneObject, SceneObject, Beat, Beat]:
    if len(instance.object_ids) != 2 or len(instance.beat_ids) != 2:
        raise DocumentError(f"{instance.template_id} records two objects and two beats")
    first_object = objects.get(instance.object_ids[0])
    second_object = objects.get(instance.object_ids[1])
    first_beat = beats.get(instance.beat_ids[0])
    second_beat = beats.get(instance.beat_ids[1])
    if first_object is None or second_object is None:
        missing = instance.object_ids[0] if first_object is None else instance.object_ids[1]
        raise DocumentError(f"template points at missing object {missing}")
    if first_beat is None or second_beat is None:
        missing_beat = instance.beat_ids[0] if first_beat is None else instance.beat_ids[1]
        raise DocumentError(f"template points at missing beat {missing_beat}")
    return first_object, second_object, first_beat, second_beat


def _check_axes_template(
    instance: TemplateInstance,
    objects: dict[str, SceneObject],
    beats: dict[str, Beat],
) -> None:
    axes, plot, first, second = _linked(instance, objects, beats)
    if axes.type != "axes" or plot.type != "function_plot":
        raise DocumentError("axes_function must own an axes and a function plot")
    if plot.params.get("axesId") != axes.id:
        raise DocumentError("axes_function plot must use its axes")
    if first.action != "create" or first.targets != [axes.id]:
        raise DocumentError("axes_function axes beat must create the axes")
    if second.action != "create" or second.targets != [plot.id]:
        raise DocumentError("axes_function plot beat must create the plot")


def _check_formula_template(
    instance: TemplateInstance,
    objects: dict[str, SceneObject],
    beats: dict[str, Beat],
) -> None:
    source, dest, first, second = _linked(instance, objects, beats)
    if source.type != "math_formula" or dest.type != "math_formula":
        raise DocumentError("formula_transform must own two formulas")
    if first.action != "write" or first.targets != [source.id]:
        raise DocumentError("formula_transform starts with a write")
    if second.action != "transform_formula" or second.targets != [source.id, dest.id]:
        raise DocumentError("formula_transform ends with the transform")


def _check_three_d_template(
    project: Project,
    instance: TemplateInstance,
    objects: dict[str, SceneObject],
    beats: dict[str, Beat],
) -> None:
    axes, child, first, second = _linked(instance, objects, beats)
    mode = instance.params.get("mode")
    expected = "surface" if mode == "surface" else "parametric_curve"
    if axes.type != "three_d_axes" or child.type != expected:
        raise DocumentError("three_d_axes must own the axes and one surface or curve")
    if child.params.get("axesId") != axes.id:
        raise DocumentError("the 3D child must use its axes")
    if first.action != "create" or first.targets != [axes.id]:
        raise DocumentError("the 3D axes beat must create the axes")
    if second.action != "create" or second.targets != [child.id]:
        raise DocumentError("the 3D child beat must create the surface or curve")
    camera = project.scene.camera
    if camera is None:
        raise DocumentError("a 3D scene needs a camera")
    if (
        camera.phi != instance.params["phi"]
        or camera.theta != instance.params["theta"]
        or camera.zoom != instance.params["zoom"]
    ):
        raise DocumentError("the 3D camera must match the template")


def _sync_axes_function(project: Project, instance: TemplateInstance) -> None:
    objects = {item.id: item for item in project.scene.objects}
    if len(instance.object_ids) != 2:
        raise DocumentError("axes_function must own the axes and the plot")
    axes = objects.get(instance.object_ids[0])
    plot = objects.get(instance.object_ids[1])
    if axes is None or plot is None:
        raise DocumentError("axes_function points at a missing object")
    if axes.type != "axes" or plot.type != "function_plot":
        raise DocumentError("axes_function must own an axes and a function plot")
    axes.params["xRange"] = _copy_numbers(instance.params["xRange"])
    axes.params["yRange"] = _copy_numbers(instance.params["yRange"])
    plot.params["expression"] = instance.params["expression"]
    plot.params["color"] = instance.params["color"]


def _sync_formula_transform(project: Project, instance: TemplateInstance) -> None:
    objects = {item.id: item for item in project.scene.objects}
    if len(instance.object_ids) != 2:
        raise DocumentError("formula_transform must own two formulas")
    source = objects.get(instance.object_ids[0])
    dest = objects.get(instance.object_ids[1])
    if source is None or dest is None:
        raise DocumentError("formula_transform points at a missing object")
    if source.type != "math_formula" or dest.type != "math_formula":
        raise DocumentError("formula_transform must own two formulas")
    source.params["latex"] = instance.params["sourceLatex"]
    source.params["color"] = instance.params["sourceColor"]
    source.params["anchor"] = _formula_side(instance.params, "source", "anchor")
    source.params["shift"] = _formula_side(instance.params, "source", "shift")
    dest.params["latex"] = instance.params["destinationLatex"]
    dest.params["color"] = instance.params["destinationColor"]
    dest.params["anchor"] = _formula_side(instance.params, "destination", "anchor")
    dest.params["shift"] = _formula_side(instance.params, "destination", "shift")


def _sync_three_d(project: Project, instance: TemplateInstance) -> None:
    """Rewrite the 3D axes, the surface or curve, and the camera from the template."""
    if len(instance.object_ids) != 2 or len(instance.beat_ids) != 2:
        raise DocumentError("three_d_axes records two objects and two beats")
    mode = instance.params.get("mode")
    if mode not in {"surface", "curve"}:
        raise DocumentError(f"unknown 3D mode {mode!r}")
    expected = "surface" if mode == "surface" else "parametric_curve"
    objects = {item.id: item for item in project.scene.objects}
    axes = objects.get(instance.object_ids[0])
    if axes is None or axes.type != "three_d_axes":
        raise DocumentError("three_d_axes template must own the axes")
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


def _require_axes(objects: dict[str, SceneObject], item: SceneObject, axes_type: str) -> None:
    axes_id = item.params.get("axesId")
    axes = objects.get(str(axes_id))
    if not isinstance(axes_id, str) or axes is None or axes.type != axes_type:
        raise DocumentError(f"{item.id} must use a {axes_type} object")


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


def _reject_start(value: object) -> None:
    if isinstance(value, dict):
        if "start" in value:
            raise DocumentError('project.json contains "start", which is not allowed')
        for item in value.values():
            _reject_start(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_start(item)


def _check_keys(raw: dict[str, object], allowed: set[str], path: str) -> None:
    for key in raw:
        if key == "start":
            raise DocumentError('project.json contains "start", which is not allowed')
        if key not in allowed:
            raise DocumentError(f'unknown key "{key}" in {path}')


def _require(raw: dict[str, object], keys: set[str], path: str) -> None:
    for key in sorted(keys):
        if key not in raw:
            raise DocumentError(f'missing key "{key}" in {path}')


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


def _reject_generated_name(value: str) -> None:
    if keyword.iskeyword(value) or value in _GENERATED_NAMES:
        raise DocumentError(f"object id {value!r} is not a valid generated name")


def _id_value(value: object, path: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise DocumentError(f"invalid id {value!r}")
    return value


def _id_list(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        raise DocumentError(f"{path} must be an array")
    return [_id_value(item, path) for item in value]


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise DocumentError(f"{path} must be a string")
    return value


def _int_value(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentError(f"{path} must be an integer")
    return value


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise DocumentError(f"{path} must be a boolean")
    return value


def _number(value: object, path: str) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError(f"{path} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise DocumentError(f"{path} must be a finite number")
    return value


def _numbers(value: object, path: str, count: int) -> list[Number]:
    if not isinstance(value, list) or len(value) != count:
        raise DocumentError(f"{path} must be {count} numbers")
    return [_number(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _range3(value: object, path: str) -> list[Number]:
    numbers = _numbers(value, path, 3)
    if float(numbers[1]) <= float(numbers[0]):
        raise DocumentError(f"{path} max must be greater than min")
    if float(numbers[2]) <= 0:
        raise DocumentError(f"{path} step must be positive")
    return numbers


def _positive(value: object, path: str) -> Number:
    number = _number(value, path)
    if float(number) <= 0:
        raise DocumentError(f"{path} must be positive")
    return number


def _duration(value: object, path: str) -> Number:
    number = _number(value, path)
    if float(number) <= 0:
        raise DocumentError(f"{path} must be positive")
    return number


def _shift(value: object, path: str) -> list[Number]:
    return _numbers(value, path, 2)


def _color(value: object, path: str) -> str:
    if not isinstance(value, str) or _COLOR.fullmatch(value) is None:
        raise DocumentError(f"{path} must be a #RRGGBB color")
    return value.upper()


def _anchor(value: object, path: str) -> str:
    if not isinstance(value, str) or value not in ANCHORS:
        raise DocumentError(f"unknown anchor {value!r}")
    return value


def parse_resolution(value: object, path: str = "resolution") -> list[int]:
    """Two integers from 1 to 24. A larger value is refused and nothing is stored."""
    if not isinstance(value, list) or len(value) != 2:
        raise DocumentError(f"{path} must be two integers")
    numbers: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise DocumentError(f"{path} must be two integers")
        if isinstance(item, float) and not item.is_integer():
            raise DocumentError(f"{path} must be two integers")
        number = int(item)
        if number > MAX_RESOLUTION:
            raise DocumentError("Resolution is above the 24×24 cap.")
        if number < 1:
            raise DocumentError(f"{path} must be from 1 to 24")
        numbers.append(number)
    return numbers


def _expression(value: object, path: str, variables: frozenset[str]) -> str:
    text = _string(value, path)
    assert_supported(text, variables)
    return text


def _pair_range(value: object, path: str) -> list[Number]:
    numbers = _numbers(value, path, 2)
    if float(numbers[1]) <= float(numbers[0]):
        raise DocumentError(f"{path} max must be greater than min")
    return numbers


def _components(value: object, path: str) -> list[str]:
    if not isinstance(value, list) or len(value) != 3:
        raise DocumentError(f"{path} must be three expressions")
    return [_expression(item, f"{path}[{index}]", CURVE_VARIABLES) for index, item in enumerate(value)]


def _parse_camera(value: object, path: str) -> Camera:
    if not isinstance(value, dict):
        raise DocumentError(f"{path} must be an object")
    _check_keys(value, _CAMERA_KEYS, path)
    _require(value, _CAMERA_KEYS, path)
    return Camera(
        phi=_number(value["phi"], f"{path}.phi"),
        theta=_number(value["theta"], f"{path}.theta"),
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
