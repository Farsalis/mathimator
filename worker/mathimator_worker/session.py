"""Document edits, undo, and derived dirty flags.

Undo history lives in this process only. It is not written to ``project.json``
and it is discarded when the process exits. The stack keeps the 100 most
recent edits, twice the 50 the spec asks for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mathimator_worker.codegen import CodegenError, generate
from mathimator_worker.document import (
    AXES_CHILD_TYPES,
    CURVE_VARIABLES,
    ENTRANCES,
    MAX_TEXT_LENGTH,
    MAX_TOTAL_SECONDS,
    MIN_BEAT_SECONDS,
    PLOT_VARIABLES,
    SURFACE_VARIABLES,
    Beat,
    ExportProfile,
    ProfileName,
    Project,
    SceneObject,
    TemplateInstance,
    beat_by_id,
    derived_starts,
    dumps_project,
    load_text,
    object_by_id,
    parse_anchor,
    parse_axis_range,
    parse_color,
    parse_latex,
    parse_number,
    parse_resolution,
    parse_text,
    project_dict,
    quantize,
    sync_three_d,
    total_duration,
)
from mathimator_worker.document_error import DocumentError
from mathimator_worker.expr import assert_supported
from mathimator_worker.identity import beat_cache_keys, scene_stamp
from mathimator_worker.jsonio import JsonValue, dumps_document
from mathimator_worker.preview_index import PreviewIndex
from mathimator_worker.templates import (
    DEFAULT_LATEX,
    DEFAULT_TEXT,
    add_axes_function,
    add_formula,
    add_formula_transform,
    add_text,
    add_three_d_axes,
)

UNDO_LIMIT = 100
MAX_NAME_LENGTH = 200


@dataclass
class Rendered:
    stamp: str
    keys: list[str]


@dataclass
class EditResult:
    focus_beat_id: str | None = None
    focus_object_id: str | None = None


# One edit operation: it applies the fields of an edit to a document in place.
Edit = Callable[[Project, dict[str, object]], EditResult]


class Session:
    def __init__(self, project: Project) -> None:
        self.project = project
        self._undo: list[Project] = []
        self._redo: list[Project] = []
        self.rendered: dict[str, Rendered] = {}

    def edit(self, raw: dict[str, object]) -> EditResult:
        project, result = self.edited(raw)
        self.commit(project)
        return result

    def edited(self, raw: dict[str, object]) -> tuple[Project, EditResult]:
        """The document ``raw`` makes, and what to focus. Raises DocumentError; the session is unchanged.

        The edited document is read back as project.json is, which checks it
        whole. Reading it back must not change it: load rewrites every field
        a template owns from the template, so an edit that changed such a
        field without its template would be accepted and lost (F072). Such an
        edit is refused with ``invalid_edit``, naming the field's JSON path.
        """
        trial = load_text(dumps_project(self.project))
        try:
            result = apply_edit(trial, raw)
        except DocumentError as exc:
            raise _edit_refusal(exc, exc.field) from exc
        try:
            reloaded = load_text(dumps_project(trial))
        except DocumentError as exc:
            # A whole-document rule, such as plot sampling, has no edit field to name.
            raise _edit_refusal(exc, None) from exc
        owned = _changed_path(project_dict(trial), project_dict(reloaded))
        if owned is not None:
            raise DocumentError(f"{owned} is set by its template, so this edit cannot change it.", code="invalid_edit")
        return reloaded, result

    def commit(self, project: Project) -> None:
        """Make ``project`` the document as one undo step. Redo is cleared."""
        self._undo.append(self.project)
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo.clear()
        self.project = project

    def undo_target(self) -> Project:
        """The document undo goes back to. Raises DocumentError when there is none."""
        if not self._undo:
            raise DocumentError("Nothing to undo.", code="nothing_to_undo")
        return self._undo[-1]

    def redo_target(self) -> Project:
        """The document redo goes forward to. Raises DocumentError when there is none."""
        if not self._redo:
            raise DocumentError("Nothing to redo.", code="nothing_to_redo")
        return self._redo[-1]

    def undo(self) -> None:
        project = self.undo_target()
        self._undo.pop()
        self._redo.append(self.project)
        self.project = project

    def redo(self) -> None:
        project = self.redo_target()
        self._redo.pop()
        self._undo.append(self.project)
        self.project = project

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def mark_rendered(self, profile: ProfileName) -> None:
        self.rendered[profile] = Rendered(
            stamp=scene_stamp(self.project),
            keys=beat_cache_keys(self.project, profile),
        )

    def dirty_flags(self, profile: ProfileName) -> list[bool]:
        """Whether each beat is dirty: every beat from the first that differs from the last render of ``profile``."""
        count = len(self.project.scene.beats)
        if count == 0:
            return []
        record = self.rendered.get(profile)
        if record is None or record.stamp != scene_stamp(self.project):
            return [True] * count
        keys = beat_cache_keys(self.project, profile)
        shared = min(len(keys), len(record.keys))
        mismatch = next((index for index in range(shared) if keys[index] != record.keys[index]), shared)
        return [index >= mismatch for index in range(count)]

    def output_current(self, profile: ProfileName) -> bool:
        """Whether the last full render of ``profile`` was of the document as it is now, beat for beat (F010).

        Unlike the dirty flags, a document with fewer beats than that render does not count.
        """
        record = self.rendered.get(profile)
        return (
            record is not None
            and record.stamp == scene_stamp(self.project)
            and record.keys == beat_cache_keys(self.project, profile)
        )

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

    def state_event(self, job_id: str, project_path: str, index: PreviewIndex | None) -> dict[str, object]:
        """The ``state`` reply, with the preview index the supervisor keeps (C5.1)."""
        durations = [beat.duration for beat in self.project.scene.beats]
        try:
            source: str | None = generate(self.project)
            source_error = None
        except CodegenError as exc:
            source = None
            source_error = str(exc)
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
            "index": None if index is None else index.to_dict(),
            # The generated scene.py, which View code shows (D25).
            "source": source,
        }
        if source_error is not None:
            payload["sourceError"] = source_error
        return payload


def apply_edit(project: Project, raw: dict[str, object]) -> EditResult:
    """Apply the edit ``raw`` to ``project`` in place. Raises DocumentError naming the edit field at fault."""
    op = raw.get("op")
    edit = _EDITS.get(op) if isinstance(op, str) else None
    if edit is None:
        raise DocumentError(f"Unknown edit operation {op}.", field="op")
    return edit(project, raw)


def _edit_refusal(exc: DocumentError, field: str | None) -> DocumentError:
    """The refusal of an edit: a document rule becomes ``invalid_edit``, and ``field`` is an edit field."""
    code = "invalid_edit" if exc.code == "invalid_document" else exc.code
    return DocumentError(str(exc), code=code, field=field)


def _changed_path(edited: JsonValue, reloaded: JsonValue, path: str = "") -> str | None:
    """The JSON path of the first value of ``edited`` that ``reloaded`` writes differently, or None."""
    if isinstance(edited, dict) and isinstance(reloaded, dict) and list(edited) == list(reloaded):
        children = [(item, reloaded[key], f"{path}.{key}" if path else key) for key, item in edited.items()]
    elif isinstance(edited, list) and isinstance(reloaded, list) and len(edited) == len(reloaded):
        children = [(item, reloaded[index], f"{path}[{index}]") for index, item in enumerate(edited)]
    else:
        return None if dumps_document(edited) == dumps_document(reloaded) else path
    return next((found for child in children if (found := _changed_path(*child)) is not None), None)


def _set_project_name(project: Project, raw: dict[str, object]) -> EditResult:
    project.name = _name(raw.get("name"), "name")
    return EditResult()


def _set_object_name(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    item.name = _name(raw.get("name"), "name")
    return EditResult(focus_object_id=item.id)


def _set_object_color(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    if item.type == "three_d_axes":
        raise DocumentError("3D axes have no color field", field="objectId")
    color = parse_color(raw.get("color"), "color")
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
    item = _object(project, raw)
    expression = parse_text(raw.get("expression"), "expression")
    if item.type == "function_plot":
        assert_supported(expression, PLOT_VARIABLES, "expression")
        item.params["expression"] = expression
        _template_plot(project, item.id, "expression", expression)
        return EditResult(focus_object_id=item.id)
    if item.type == "surface":
        assert_supported(expression, SURFACE_VARIABLES, "expression")
        _three_d_param(project, item.id, "expression", expression)
        return EditResult(focus_object_id=item.id)
    raise DocumentError("only a plot or a surface has an expression", field="objectId")


def _set_latex(project: Project, raw: dict[str, object]) -> EditResult:
    item = _formula(project, raw)
    item.params["latex"] = parse_latex(raw.get("latex"), "latex")
    _sync_formula_fields(project, item)
    return EditResult(focus_object_id=item.id)


def _set_text(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    if item.type != "text":
        raise DocumentError("only a text object has text", field="objectId")
    item.params["text"] = parse_text(raw.get("text"), "text", MAX_TEXT_LENGTH)
    return EditResult(focus_object_id=item.id)


def _set_anchor(project: Project, raw: dict[str, object]) -> EditResult:
    item = _positioned(project, raw)
    item.params["anchor"] = parse_anchor(raw.get("anchor"), "anchor")
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
    item = _object(project, raw)
    if item.type not in {"axes", "three_d_axes"}:
        raise DocumentError("only axes have a range", field="objectId")
    axis = raw.get("axis")
    if item.type == "axes" and axis not in ("x", "y"):
        raise DocumentError("axis must be x or y", field="axis")
    if item.type == "three_d_axes" and axis not in ("x", "y", "z"):
        raise DocumentError("axis must be x, y, or z", field="axis")
    key = {"x": "xRange", "y": "yRange", "z": "zRange"}[str(axis)]
    value: list[JsonValue] = [_finite(number, "value") for number in parse_axis_range(raw.get("value"), "value")]
    item.params[key] = value
    if item.type == "axes":
        _template_axes(project, item.id, key, value)
    else:
        _three_d_param(project, item.id, key, value)
    return EditResult(focus_object_id=item.id)


def _set_length(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    if item.type != "axes":
        raise DocumentError("only axes have a length", field="objectId")
    axis = raw.get("axis")
    if axis not in ("x", "y"):
        raise DocumentError("axis must be x or y", field="axis")
    value = _positive_number(raw.get("value"), "value")
    item.params["xLength" if axis == "x" else "yLength"] = value
    return EditResult(focus_object_id=item.id)


def _set_show_labels(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    if item.type != "axes":
        raise DocumentError("only axes have labels", field="objectId")
    value = raw.get("value")
    if not isinstance(value, bool):
        raise DocumentError("value must be a boolean", field="value")
    item.params["showLabels"] = value
    return EditResult(focus_object_id=item.id)


def _set_domain(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    if item.type != "function_plot":
        raise DocumentError("only a function plot has a domain", field="objectId")
    x_min = _finite(raw.get("xMin"), "xMin")
    x_max = _finite(raw.get("xMax"), "xMax")
    if float(x_max) <= float(x_min):
        raise DocumentError("xMax must be greater than xMin", field="xMax")
    item.params["xMin"] = x_min
    item.params["xMax"] = x_max
    return EditResult(focus_object_id=item.id)


def _set_beat_duration(project: Project, raw: dict[str, object]) -> EditResult:
    beat = _beat(project, raw)
    duration = _finite(raw.get("duration"), "duration")
    if float(duration) < MIN_BEAT_SECONDS:
        message = f"duration must be at least {MIN_BEAT_SECONDS:g} seconds"
        raise DocumentError(message, code="limit_exceeded", field="duration")
    others = sum(float(item.duration) for item in project.scene.beats if item is not beat)
    if others + float(duration) > MAX_TOTAL_SECONDS + 1e-9:
        message = f"total duration must be at most {MAX_TOTAL_SECONDS:g} seconds"
        raise DocumentError(message, code="limit_exceeded", field="duration")
    beat.duration = duration
    return EditResult(focus_beat_id=beat.id)


def _set_beat_color(project: Project, raw: dict[str, object]) -> EditResult:
    beat = _param_beat(project, raw, "set_color")
    color = parse_color(raw.get("color"), "color")
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
    if mode not in ("surface", "curve"):
        raise DocumentError("mode must be surface or curve", field="mode")
    instance = _require_three_d(project)
    instance.params["mode"] = mode
    sync_three_d(project, instance)
    return EditResult(focus_beat_id=instance.beat_ids[1], focus_object_id=instance.object_ids[1])


def _set_camera(project: Project, raw: dict[str, object]) -> EditResult:
    instance = _require_three_d(project)
    key = raw.get("key")
    if key not in ("phi", "theta", "zoom"):
        raise DocumentError("key must be phi, theta, or zoom", field="key")
    value = _positive_number(raw.get("value"), "value") if key == "zoom" else _finite(raw.get("value"), "value")
    instance.params[str(key)] = value
    sync_three_d(project, instance)
    return EditResult(focus_object_id=instance.object_ids[0])


def _set_resolution(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    if item.type != "surface":
        raise DocumentError("only a surface has a resolution", field="objectId")
    value = parse_resolution(raw.get("value"), "value")
    _three_d_param(project, item.id, "resolution", value)
    return EditResult(focus_object_id=item.id)


def _set_curve_component(project: Project, raw: dict[str, object]) -> EditResult:
    item = _object(project, raw)
    if item.type != "parametric_curve":
        raise DocumentError("only a curve has components", field="objectId")
    index = raw.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index not in {0, 1, 2}:
        raise DocumentError("index must be 0, 1, or 2", field="index")
    expression = parse_text(raw.get("expression"), "expression")
    assert_supported(expression, CURVE_VARIABLES, "expression")
    instance = _three_d_for_object(project, item.id)
    components = instance.params.get("components")
    if not isinstance(components, list) or len(components) != 3:
        raise DocumentError("curve components must be three expressions")
    updated = list(components)
    updated[index] = expression
    instance.params["components"] = updated
    sync_three_d(project, instance)
    return EditResult(focus_object_id=item.id)


def _set_export_profile(project: Project, raw: dict[str, object]) -> EditResult:
    """Store the export preset. Validation rejects transparent MP4 and GIF."""
    fmt = raw.get("format")
    width = raw.get("width")
    height = raw.get("height")
    fps = raw.get("fps")
    transparent = raw.get("transparent")
    if not isinstance(fmt, str):
        raise DocumentError("format must be a string", field="format")
    if isinstance(width, bool) or not isinstance(width, int):
        raise DocumentError("width must be an integer", field="width")
    if isinstance(height, bool) or not isinstance(height, int):
        raise DocumentError("height must be an integer", field="height")
    if isinstance(fps, bool) or not isinstance(fps, int):
        raise DocumentError("fps must be an integer", field="fps")
    if not isinstance(transparent, bool):
        raise DocumentError("transparent must be a boolean", field="transparent")
    project.export_profile = ExportProfile(fmt, width, height, fps, transparent)
    return EditResult()


def _add_formula(project: Project, raw: dict[str, object]) -> EditResult:
    latex = parse_latex(raw.get("latex", DEFAULT_LATEX), "latex")
    return _focus(add_formula(project, _after(raw), latex))


def _add_text(project: Project, raw: dict[str, object]) -> EditResult:
    text = parse_text(raw.get("text", DEFAULT_TEXT), "text", MAX_TEXT_LENGTH)
    return _focus(add_text(project, _after(raw), text))


def _insertion(insert: Callable[[Project, str | None], tuple[str, str]]) -> Edit:
    """The edit that inserts a template after the beat ``afterBeatId`` names."""

    def edit(project: Project, raw: dict[str, object]) -> EditResult:
        return _focus(insert(project, _after(raw)))

    return edit


def _focus(inserted: tuple[str, str]) -> EditResult:
    """Focus the beat and the object an insertion returns."""
    beat_id, object_id = inserted
    return EditResult(focus_beat_id=beat_id, focus_object_id=object_id)


def _three_d_param(project: Project, object_id: str, key: str, value: JsonValue) -> None:
    instance = _three_d_for_object(project, object_id)
    instance.params[key] = value
    sync_three_d(project, instance)


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
    object_id = _object(project, raw).id
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
            if item.id in remove or item.type not in AXES_CHILD_TYPES:
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


def _template_plot(project: Project, plot_id: str, key: str, value: JsonValue) -> None:
    for instance in project.scene.template_instances:
        if instance.template_id != "axes_function" or len(instance.object_ids) != 2:
            continue
        if instance.object_ids[1] == plot_id:
            instance.params[key] = value


def _template_axes(project: Project, axes_id: str, key: str, value: JsonValue) -> None:
    for instance in project.scene.template_instances:
        if instance.template_id != "axes_function" or not instance.object_ids:
            continue
        if instance.object_ids[0] == axes_id:
            instance.params[key] = value


def _object(project: Project, raw: dict[str, object]) -> SceneObject:
    return object_by_id(project, _id(raw.get("objectId"), "objectId"), "objectId")


def _beat(project: Project, raw: dict[str, object]) -> Beat:
    return beat_by_id(project, _id(raw.get("beatId"), "beatId"), "beatId")


def _formula(project: Project, raw: dict[str, object]) -> SceneObject:
    item = _object(project, raw)
    if item.type != "math_formula":
        raise DocumentError("only a formula has latex", field="objectId")
    return item


def _positioned(project: Project, raw: dict[str, object]) -> SceneObject:
    item = _object(project, raw)
    if item.type not in {"math_formula", "text"}:
        raise DocumentError("only a formula or text has an anchor", field="objectId")
    return item


def _param_beat(project: Project, raw: dict[str, object], action: str) -> Beat:
    beat = _beat(project, raw)
    if beat.action != action:
        raise DocumentError(f"beat {beat.id} is not {action}", field="beatId")
    return beat


def _after(raw: dict[str, object]) -> str | None:
    if "afterBeatId" not in raw or raw["afterBeatId"] is None:
        return None
    return _id(raw["afterBeatId"], "afterBeatId")


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise DocumentError(f"{label} must be an id", field=label)
    return value


def _name(value: object, label: str) -> str:
    name = parse_text(value, label, MAX_NAME_LENGTH)
    if name.strip() == "":
        raise DocumentError(f"{label} must not be empty", field=label)
    return name


def _finite(value: object, label: str) -> int | float:
    return quantize(parse_number(value, label))


def _positive_number(value: object, label: str) -> int | float:
    number = _finite(value, label)
    if float(number) <= 0:
        raise DocumentError(f"{label} must be positive", field=label)
    return number


def _shift_value(value: object) -> list[JsonValue]:
    if not isinstance(value, list) or len(value) != 2:
        raise DocumentError("shift must be two numbers", field="shift")
    return [_finite(value[0], "shift"), _finite(value[1], "shift")]


# The edit of each op (contract C4.1).
_EDITS: dict[str, Edit] = {
    "set_project_name": _set_project_name,
    "set_object_name": _set_object_name,
    "set_object_color": _set_object_color,
    "set_expression": _set_expression,
    "set_latex": _set_latex,
    "set_text": _set_text,
    "set_anchor": _set_anchor,
    "set_object_shift": _set_object_shift,
    "set_axes_range": _set_axes_range,
    "set_length": _set_length,
    "set_show_labels": _set_show_labels,
    "set_domain": _set_domain,
    "set_beat_duration": _set_beat_duration,
    "set_beat_color": _set_beat_color,
    "set_beat_shift": _set_beat_shift,
    "add_axes_function": _insertion(add_axes_function),
    "add_formula_transform": _insertion(add_formula_transform),
    "add_three_d_axes": _insertion(add_three_d_axes),
    "add_formula": _add_formula,
    "add_text": _add_text,
    "delete_object": _delete_object,
    "set_three_d_mode": _set_three_d_mode,
    "set_camera": _set_camera,
    "set_resolution": _set_resolution,
    "set_curve_component": _set_curve_component,
    "set_export_profile": _set_export_profile,
}
