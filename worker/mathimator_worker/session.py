"""Document edits, undo, and derived dirty flags.

Undo history lives in this process only. It is not written to ``project.json``
and it is discarded when the process exits. Fifty edits fit; the stack keeps
the most recent 100.
"""

from __future__ import annotations

from dataclasses import dataclass

from mathimator_worker.codegen import CodegenError, generate
from mathimator_worker.document import (
    CURVE_VARIABLES,
    SURFACE_VARIABLES,
    Beat,
    ExportProfile,
    Project,
    SceneObject,
    ENTRANCES,
    TemplateInstance,
    beat_by_id,
    beat_cache_keys,
    derived_starts,
    dumps_project,
    load_text,
    normalize_color,
    object_by_id,
    _sync_three_d,
    parse_resolution,
    project_dict,
    quantize,
    scene_stamp,
    total_duration,
    validate_project,
)
from mathimator_worker.document_error import DocumentError
from mathimator_worker.expr import assert_supported
from mathimator_worker.templates import (
    add_axes_function,
    add_formula,
    add_formula_transform,
    add_text,
    add_three_d_axes,
    assert_plot_expression,
)

UNDO_LIMIT = 100


@dataclass
class Rendered:
    stamp: str
    keys: list[str]


@dataclass
class EditResult:
    focus_beat_id: str | None = None
    focus_object_id: str | None = None


class Session:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.preview_index: dict[str, object] | None = None
        self._undo: list[Project] = []
        self._redo: list[Project] = []
        self.rendered: dict[str, Rendered] = {}

    def edit(self, raw: dict[str, object]) -> EditResult:
        if not isinstance(raw.get("op"), str):
            raise DocumentError("missing edit op")
        trial = load_text(dumps_project(self.project))
        result = apply_edit(trial, raw)
        trial = load_text(dumps_project(trial))
        validate_project(trial)
        self._undo.append(self.project)
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo.clear()
        self.project = trial
        return result

    def undo(self) -> None:
        if not self._undo:
            raise DocumentError("nothing to undo")
        self._redo.append(self.project)
        self.project = self._undo.pop()

    def redo(self) -> None:
        if not self._redo:
            raise DocumentError("nothing to redo")
        self._undo.append(self.project)
        self.project = self._redo.pop()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def mark_rendered(self, profile: str) -> None:
        self.rendered[profile] = Rendered(
            stamp=scene_stamp(self.project),
            keys=beat_cache_keys(self.project, profile),
        )

    def dirty_flags(self, profile: str) -> list[bool]:
        count = len(self.project.scene.beats)
        if count == 0:
            return []
        record = self.rendered.get(profile)
        if record is None or record.stamp != scene_stamp(self.project):
            return [True] * count
        keys = beat_cache_keys(self.project, profile)
        shared = min(len(keys), len(record.keys))
        mismatch = shared
        for index in range(shared):
            if keys[index] != record.keys[index]:
                mismatch = index
                break
        else:
            if len(keys) > len(record.keys):
                mismatch = len(record.keys)
            else:
                mismatch = len(keys)
        return [index >= mismatch for index in range(count)]

    def rendered_payload(self) -> dict[str, object]:
        return {
            profile: {"stamp": record.stamp, "keys": list(record.keys)} for profile, record in self.rendered.items()
        }

    def load_rendered(self, payload: object) -> None:
        self.rendered.clear()
        if not isinstance(payload, dict):
            return
        for profile, record in payload.items():
            if profile not in {"preview", "export"} or not isinstance(record, dict):
                continue
            stamp = record.get("stamp")
            keys = record.get("keys")
            if not isinstance(stamp, str) or not isinstance(keys, list):
                continue
            if not all(isinstance(item, str) for item in keys):
                continue
            self.rendered[profile] = Rendered(stamp=stamp, keys=list(keys))

    def state_event(self, job_id: str, project_path: str) -> dict[str, object]:
        durations = [beat.duration for beat in self.project.scene.beats]
        try:
            code: str | None = generate(self.project)
            code_error = None
        except CodegenError as exc:
            code = None
            code_error = str(exc)
        payload: dict[str, object] = {
            "event": "state",
            "jobId": job_id,
            "projectPath": project_path,
            "document": project_dict(self.project),
            "derived": {
                "starts": derived_starts(durations),
                "total": total_duration(self.project),
                "dirty": self.dirty_flags("preview"),
                "canUndo": self.can_undo(),
                "canRedo": self.can_redo(),
            },
            "index": self.preview_index,
            "code": code,
        }
        if code_error is not None:
            payload["codeError"] = code_error
        return payload


def apply_edit(project: Project, raw: dict[str, object]) -> EditResult:
    op = raw["op"]
    if op == "set_project_name":
        project.name = _name(raw.get("name"), "name")
        return EditResult()
    if op == "set_object_name":
        item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
        item.name = _name(raw.get("name"), "name")
        return EditResult(focus_object_id=item.id)
    if op == "set_object_color":
        return _set_object_color(project, raw)
    if op == "set_expression":
        return _set_expression(project, raw)
    if op == "set_latex":
        return _set_latex(project, raw)
    if op == "set_text":
        return _set_text(project, raw)
    if op == "set_anchor":
        return _set_anchor(project, raw)
    if op == "set_object_shift":
        return _set_object_shift(project, raw)
    if op == "set_axes_range":
        return _set_axes_range(project, raw)
    if op == "set_length":
        return _set_length(project, raw)
    if op == "set_show_labels":
        return _set_show_labels(project, raw)
    if op == "set_domain":
        return _set_domain(project, raw)
    if op == "set_beat_duration":
        return _set_beat_duration(project, raw)
    if op == "set_beat_color":
        return _set_beat_color(project, raw)
    if op == "set_beat_shift":
        return _set_beat_shift(project, raw)
    if op == "add_axes_function":
        beat_id, object_id = add_axes_function(project, _after(raw))
        return EditResult(focus_beat_id=beat_id, focus_object_id=object_id)
    if op == "add_formula_transform":
        beat_id, object_id = add_formula_transform(project, _after(raw))
        return EditResult(focus_beat_id=beat_id, focus_object_id=object_id)
    if op == "add_three_d_axes":
        beat_id, object_id = add_three_d_axes(project, _after(raw))
        return EditResult(focus_beat_id=beat_id, focus_object_id=object_id)
    if op == "set_three_d_mode":
        return _set_three_d_mode(project, raw)
    if op == "set_camera":
        return _set_camera(project, raw)
    if op == "set_resolution":
        return _set_resolution(project, raw)
    if op == "set_curve_component":
        return _set_curve_component(project, raw)
    if op == "set_export_profile":
        return _set_export_profile(project, raw)
    if op == "add_formula":
        latex = raw.get("latex", "y = \\sin(x)")
        if not isinstance(latex, str):
            raise DocumentError("latex must be a string")
        beat_id, object_id = add_formula(project, _after(raw), latex)
        return EditResult(focus_beat_id=beat_id, focus_object_id=object_id)
    if op == "add_text":
        text = raw.get("text", "Text")
        if not isinstance(text, str):
            raise DocumentError("text must be a string")
        beat_id, object_id = add_text(project, _after(raw), text)
        return EditResult(focus_beat_id=beat_id, focus_object_id=object_id)
    if op == "delete_object":
        return _delete_object(project, raw)
    raise DocumentError(f"unknown edit {op}")


def _set_object_color(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type == "three_d_axes":
        raise DocumentError("3D axes have no color field")
    color = normalize_color(_required_str(raw.get("color"), "color"))
    item.params["color"] = color
    if item.type == "function_plot":
        _template_plot(project, item.id, "color", color)
    elif item.type == "math_formula":
        _sync_formula_fields(project, item)
    elif item.type == "surface":
        _three_d_param(project, item.id, "surfaceColor", color)
    elif item.type == "parametric_curve":
        _three_d_param(project, item.id, "curveColor", color)
    return EditResult(focus_object_id=item.id)


def _set_expression(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    expression = _required_str(raw.get("expression"), "expression")
    if item.type == "function_plot":
        assert_plot_expression(expression)
        item.params["expression"] = expression
        _template_plot(project, item.id, "expression", expression)
        return EditResult(focus_object_id=item.id)
    if item.type == "surface":
        assert_supported(expression, SURFACE_VARIABLES)
        _three_d_param(project, item.id, "expression", expression)
        return EditResult(focus_object_id=item.id)
    raise DocumentError("only a plot or a surface has an expression")


def _set_latex(project: Project, raw: dict[str, object]) -> EditResult:
    item = _formula(project, raw)
    latex = raw.get("latex")
    if not isinstance(latex, str):
        raise DocumentError("latex must be a string")
    if len(latex) > 500:
        raise DocumentError("latex is longer than 500 characters")
    item.params["latex"] = latex
    _sync_formula_fields(project, item)
    return EditResult(focus_object_id=item.id)


def _set_text(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type != "text":
        raise DocumentError("only a text object has text")
    text = raw.get("text")
    if not isinstance(text, str):
        raise DocumentError("text must be a string")
    if len(text) > 200:
        raise DocumentError("text is longer than 200 characters")
    item.params["text"] = text
    return EditResult(focus_object_id=item.id)


def _set_anchor(project: Project, raw: dict[str, object]) -> EditResult:
    item = _positioned(project, raw)
    anchor = _required_str(raw.get("anchor"), "anchor")
    if anchor not in {"origin", "edge_up", "edge_down", "edge_left", "edge_right"}:
        raise DocumentError(f"unknown anchor {anchor!r}")
    item.params["anchor"] = anchor
    if item.type == "math_formula":
        _sync_formula_fields(project, item)
    return EditResult(focus_object_id=item.id)


def _set_object_shift(project: Project, raw: dict[str, object]) -> EditResult:
    item = _positioned(project, raw)
    item.params["shift"] = _shift_value(raw.get("shift"))
    if item.type == "math_formula":
        _sync_formula_fields(project, item)
    return EditResult(focus_object_id=item.id)


def _set_axes_range(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type not in {"axes", "three_d_axes"}:
        raise DocumentError("only axes have a range")
    axis = raw.get("axis")
    if item.type == "axes" and axis not in {"x", "y"}:
        raise DocumentError("axis must be x or y")
    if item.type == "three_d_axes" and axis not in {"x", "y", "z"}:
        raise DocumentError("axis must be x, y, or z")
    if not isinstance(axis, str):
        raise DocumentError("axis must be x, y, or z")
    key = {"x": "xRange", "y": "yRange", "z": "zRange"}[axis]
    value = _range_value(raw.get("value"))
    item.params[key] = value
    if item.type == "axes":
        _template_axes(project, item.id, key, value)
    else:
        _three_d_param(project, item.id, key, value)
    return EditResult(focus_object_id=item.id)


def _set_length(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type != "axes":
        raise DocumentError("only axes have a length")
    axis = raw.get("axis")
    if axis not in {"x", "y"}:
        raise DocumentError("axis must be x or y")
    value = _positive_number(raw.get("value"), "value")
    item.params["xLength" if axis == "x" else "yLength"] = value
    return EditResult(focus_object_id=item.id)


def _set_show_labels(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type != "axes":
        raise DocumentError("only axes have labels")
    value = raw.get("value")
    if not isinstance(value, bool):
        raise DocumentError("showLabels must be a boolean")
    item.params["showLabels"] = value
    return EditResult(focus_object_id=item.id)


def _set_domain(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type != "function_plot":
        raise DocumentError("only a function plot has a domain")
    x_min = _finite(raw.get("xMin"), "xMin")
    x_max = _finite(raw.get("xMax"), "xMax")
    if float(x_max) <= float(x_min):
        raise DocumentError("xMax must be greater than xMin")
    item.params["xMin"] = x_min
    item.params["xMax"] = x_max
    return EditResult(focus_object_id=item.id)


def _set_beat_duration(project: Project, raw: dict[str, object]) -> EditResult:
    beat = beat_by_id(project, _id(raw.get("beatId"), "beatId"))
    duration = _positive_number(raw.get("duration"), "duration")
    if float(duration) < 0.1:
        raise DocumentError("beat duration must be at least 0.1 seconds")
    others = sum(float(item.duration) for item in project.scene.beats if item is not beat)
    if others + float(duration) > 120.0 + 1e-9:
        raise DocumentError("total duration must be at most 120 seconds")
    beat.duration = duration
    return EditResult(focus_beat_id=beat.id)


def _set_beat_color(project: Project, raw: dict[str, object]) -> EditResult:
    beat = _param_beat(project, raw, "set_color")
    color = normalize_color(_required_str(raw.get("color"), "color"))
    if beat.params is None:
        beat.params = {}
    beat.params["color"] = color
    return EditResult(focus_beat_id=beat.id)


def _set_beat_shift(project: Project, raw: dict[str, object]) -> EditResult:
    beat = _param_beat(project, raw, "move")
    if beat.params is None:
        beat.params = {}
    beat.params["shift"] = _shift_value(raw.get("shift"))
    return EditResult(focus_beat_id=beat.id)


def _set_three_d_mode(project: Project, raw: dict[str, object]) -> EditResult:
    mode = raw.get("mode")
    if mode not in {"surface", "curve"}:
        raise DocumentError(f"unknown 3D mode {mode!r}")
    instance = _require_three_d(project)
    instance.params["mode"] = mode
    _sync_three_d(project, instance)
    return EditResult(focus_beat_id=instance.beat_ids[1], focus_object_id=instance.object_ids[1])


def _set_camera(project: Project, raw: dict[str, object]) -> EditResult:
    instance = _require_three_d(project)
    key = raw.get("key")
    if key not in {"phi", "theta", "zoom"}:
        raise DocumentError("camera field must be phi, theta, or zoom")
    if key == "zoom":
        value = _positive_number(raw.get("value"), "zoom")
    else:
        value = _finite(raw.get("value"), str(key))
    instance.params[key] = value
    _sync_three_d(project, instance)
    return EditResult(focus_object_id=instance.object_ids[0])


def _set_resolution(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type != "surface":
        raise DocumentError("only a surface has a resolution")
    value = parse_resolution(raw.get("value"))
    _three_d_param(project, item.id, "resolution", value)
    return EditResult(focus_object_id=item.id)


def _set_curve_component(project: Project, raw: dict[str, object]) -> EditResult:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type != "parametric_curve":
        raise DocumentError("only a curve has components")
    index = raw.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index not in {0, 1, 2}:
        raise DocumentError("component index must be 0, 1, or 2")
    expression = _required_str(raw.get("expression"), "expression")
    assert_supported(expression, CURVE_VARIABLES)
    instance = _three_d_for_object(project, item.id)
    components = instance.params.get("components")
    if not isinstance(components, list) or len(components) != 3:
        raise DocumentError("curve components must be three expressions")
    updated = list(components)
    updated[index] = expression
    instance.params["components"] = updated
    _sync_three_d(project, instance)
    return EditResult(focus_object_id=item.id)


def _set_export_profile(project: Project, raw: dict[str, object]) -> EditResult:
    """Store the export preset. Validation rejects transparent MP4 and GIF."""
    fmt = raw.get("format")
    width = raw.get("width")
    height = raw.get("height")
    fps = raw.get("fps")
    transparent = raw.get("transparent")
    if not isinstance(fmt, str):
        raise DocumentError("format must be a string")
    if isinstance(width, bool) or not isinstance(width, int):
        raise DocumentError("width must be an integer")
    if isinstance(height, bool) or not isinstance(height, int):
        raise DocumentError("height must be an integer")
    if isinstance(fps, bool) or not isinstance(fps, int):
        raise DocumentError("fps must be an integer")
    if not isinstance(transparent, bool):
        raise DocumentError("transparent must be a boolean")
    project.export_profile = ExportProfile(fmt, width, height, fps, transparent)
    return EditResult()


def _three_d_param(project: Project, object_id: str, key: str, value: object) -> None:
    instance = _three_d_for_object(project, object_id)
    instance.params[key] = value  # type: ignore[assignment]
    _sync_three_d(project, instance)


def _require_three_d(project: Project) -> TemplateInstance:
    matches = [item for item in project.scene.template_instances if item.template_id == "three_d_axes"]
    if len(matches) != 1:
        raise DocumentError("This scene has no 3D template.")
    return matches[0]


def _three_d_for_object(project: Project, object_id: str) -> TemplateInstance:
    for instance in project.scene.template_instances:
        if instance.template_id == "three_d_axes" and object_id in instance.object_ids:
            return instance
    raise DocumentError("This scene has no 3D template.")


def _sync_formula_fields(project: Project, item: SceneObject) -> None:
    for instance in project.scene.template_instances:
        if instance.template_id != "formula_transform" or len(instance.object_ids) != 2:
            continue
        if instance.object_ids[0] == item.id:
            instance.params["sourceLatex"] = item.params["latex"]
            instance.params["sourceColor"] = item.params["color"]
            _store_formula_side(instance, "source", item)
        elif instance.object_ids[1] == item.id:
            instance.params["destinationLatex"] = item.params["latex"]
            instance.params["destinationColor"] = item.params["color"]
            _store_formula_side(instance, "destination", item)
        else:
            continue
        return


def _store_formula_side(instance: TemplateInstance, side: str, item: SceneObject) -> None:
    anchor_key = f"{side}Anchor"
    shift_key = f"{side}Shift"
    instance.params[anchor_key] = item.params.get("anchor", "edge_up")
    shift = item.params.get("shift", [0, 0])
    instance.params[shift_key] = list(shift) if isinstance(shift, list) else [0, 0]


def _delete_object(project: Project, raw: dict[str, object]) -> EditResult:
    object_id = _id(raw.get("objectId"), "objectId")
    object_by_id(project, object_id)
    remove = {object_id}
    _expand_removal(project, remove)
    project.scene.objects = [item for item in project.scene.objects if item.id not in remove]
    project.scene.beats = [beat for beat in project.scene.beats if not any(target in remove for target in beat.targets)]
    project.scene.template_instances = [
        instance
        for instance in project.scene.template_instances
        if not any(item_id in remove for item_id in instance.object_ids)
    ]
    if project.scene.kind == "three_d_scene" and not any(
        instance.template_id == "three_d_axes" for instance in project.scene.template_instances
    ):
        project.scene.kind = "scene"
        project.scene.camera = None
    return EditResult()


def _expand_removal(project: Project, remove: set[str]) -> None:
    changed = True
    while changed:
        changed = False
        for item in project.scene.objects:
            if item.id in remove or item.type not in {"function_plot", "surface", "parametric_curve"}:
                continue
            axes_id = item.params.get("axesId")
            if isinstance(axes_id, str) and axes_id in remove:
                remove.add(item.id)
                changed = True
        for instance in project.scene.template_instances:
            if instance.template_id != "three_d_axes":
                continue
            if not any(item_id in remove for item_id in instance.object_ids):
                continue
            for item_id in instance.object_ids:
                if item_id not in remove:
                    remove.add(item_id)
                    changed = True
        entrances = _entrance_indexes(project)
        removed_beats = {
            index for index, beat in enumerate(project.scene.beats) if any(target in remove for target in beat.targets)
        }
        for item_id, index in entrances.items():
            if index in removed_beats and item_id not in remove:
                remove.add(item_id)
                changed = True


def _entrance_indexes(project: Project) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, beat in enumerate(project.scene.beats):
        if beat.action not in ENTRANCES:
            continue
        if beat.action == "transform_formula" and len(beat.targets) == 2:
            found.setdefault(beat.targets[1], index)
            continue
        if len(beat.targets) == 1:
            found.setdefault(beat.targets[0], index)
    return found


def _template_plot(project: Project, plot_id: str, key: str, value: object) -> None:
    for instance in project.scene.template_instances:
        if instance.template_id != "axes_function" or len(instance.object_ids) != 2:
            continue
        if instance.object_ids[1] == plot_id:
            instance.params[key] = value  # type: ignore[assignment]


def _template_axes(project: Project, axes_id: str, key: str, value: object) -> None:
    for instance in project.scene.template_instances:
        if instance.template_id != "axes_function" or not instance.object_ids:
            continue
        if instance.object_ids[0] == axes_id:
            instance.params[key] = value  # type: ignore[assignment]


def _formula(project: Project, raw: dict[str, object]) -> SceneObject:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type != "math_formula":
        raise DocumentError("only a formula has latex")
    return item


def _positioned(project: Project, raw: dict[str, object]) -> SceneObject:
    item = object_by_id(project, _id(raw.get("objectId"), "objectId"))
    if item.type not in {"math_formula", "text"}:
        raise DocumentError("only a formula or text has an anchor")
    return item


def _param_beat(project: Project, raw: dict[str, object], action: str) -> Beat:
    beat = beat_by_id(project, _id(raw.get("beatId"), "beatId"))
    if beat.action != action:
        raise DocumentError(f"beat {beat.id} is not {action}")
    return beat


def _after(raw: dict[str, object]) -> str | None:
    if "afterBeatId" not in raw or raw["afterBeatId"] is None:
        return None
    return _id(raw["afterBeatId"], "afterBeatId")


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise DocumentError(f"{label} must be an id")
    return value


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise DocumentError(f"{label} must be a non-empty string")
    if len(value) > 200:
        raise DocumentError(f"{label} is longer than 200 characters")
    return value


def _required_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise DocumentError(f"{label} must be a string")
    return value


def _finite(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError(f"{label} must be a number")
    quantized = quantize(float(value))
    return quantized


def _positive_number(value: object, label: str) -> int | float:
    number = _finite(value, label)
    if float(number) <= 0:
        raise DocumentError(f"{label} must be positive")
    return number


def _shift_value(value: object) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 2:
        raise DocumentError("shift must be two numbers")
    return [_finite(value[0], "shift"), _finite(value[1], "shift")]


def _range_value(value: object) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 3:
        raise DocumentError("range must be min, max, and step")
    numbers = [_finite(item, "range") for item in value]
    if float(numbers[1]) <= float(numbers[0]):
        raise DocumentError("range max must be greater than min")
    if float(numbers[2]) <= 0:
        raise DocumentError("range step must be positive")
    return numbers
