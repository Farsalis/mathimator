"""Insert the v1 templates plus a single formula or text object."""

from __future__ import annotations

from mathimator_worker.document import (
    Camera,
    Beat,
    Project,
    SceneObject,
    TemplateInstance,
    beat_by_id,
    fresh_id,
    used_ids,
)
from mathimator_worker.document_error import DocumentError
from mathimator_worker.expr import assert_supported

_AXES_X_RANGE = [-6.5, 6.5, 1]
_AXES_Y_RANGE = [-1.5, 1.5, 0.5]
_PLOT_COLOR = "#58C4DD"
_DEFAULT_LATEX = "y = \\sin(x)"
_SOURCE_LATEX = "y = \\sin(x)"
_DEST_LATEX = "y = \\cos(x)"
_FORMULA_COLOR = "#FFFFFF"
_THREE_D_X_RANGE = [-3, 3, 1]
_THREE_D_Y_RANGE = [-3, 3, 1]
_THREE_D_Z_RANGE = [-2, 2, 1]
_SURFACE_EXPRESSION = "sin(x)*cos(y)"
_SURFACE_RANGE = [-2.5, 2.5]
_SURFACE_RESOLUTION = [8, 8]
_SURFACE_COLOR = "#58C4DD"
_CURVE_COMPONENTS = ["cos(t)", "sin(t)", "t / 3"]
_CURVE_RANGE = [0, 12.56]
_CURVE_COLOR = "#F2CC8F"
_CAMERA_PHI = 65
_CAMERA_THETA = -45
_CAMERA_ZOOM = 1


def add_axes_function(project: Project, after_beat_id: str | None) -> tuple[str, str]:
    """Insert axes and ``sin(x)``. Returns the plot's beat id and object id."""
    if project.scene.kind != "scene":
        raise DocumentError("This template is for a 2D scene.")
    used = used_ids(project)
    axes_id = fresh_id("axes", used)
    plot_id = fresh_id("plot", used)
    axes_beat_id = fresh_id("beat", used)
    plot_beat_id = fresh_id("beat", used)
    template_id = fresh_id("tmpl", used)
    x_range = list(_AXES_X_RANGE)
    y_range = list(_AXES_Y_RANGE)
    project.scene.objects.append(
        SceneObject(
            id=axes_id,
            name="Axes",
            type="axes",
            params={
                "xRange": x_range,
                "yRange": y_range,
                "xLength": 10,
                "yLength": 4,
                "color": "#FFFFFF",
                "showLabels": True,
            },
        )
    )
    project.scene.objects.append(
        SceneObject(
            id=plot_id,
            name="Sine",
            type="function_plot",
            params={
                "axesId": axes_id,
                "expression": "sin(x)",
                "xMin": -6.28,
                "xMax": 6.28,
                "color": _PLOT_COLOR,
            },
        )
    )
    beats = [
        Beat(id=axes_beat_id, duration=2, action="create", targets=[axes_id]),
        Beat(id=plot_beat_id, duration=2, action="create", targets=[plot_id]),
    ]
    _insert_beats(project, beats, after_beat_id)
    project.scene.template_instances.append(
        TemplateInstance(
            id=template_id,
            template_id="axes_function",
            params={
                "expression": "sin(x)",
                "xRange": list(x_range),
                "yRange": list(y_range),
                "color": _PLOT_COLOR,
            },
            object_ids=[axes_id, plot_id],
            beat_ids=[axes_beat_id, plot_beat_id],
        )
    )
    return plot_beat_id, plot_id


def add_formula_transform(project: Project, after_beat_id: str | None) -> tuple[str, str]:
    """Insert two formulas, a write, and a transform. Returns the transform beat and destination."""
    if project.scene.kind != "scene":
        raise DocumentError("This template is for a 2D scene.")
    used = used_ids(project)
    source_id = fresh_id("formula", used)
    dest_id = fresh_id("formula", used)
    write_id = fresh_id("beat", used)
    transform_id = fresh_id("beat", used)
    template_id = fresh_id("tmpl", used)
    project.scene.objects.append(
        SceneObject(
            id=source_id,
            name="Formula",
            type="math_formula",
            params={
                "latex": _SOURCE_LATEX,
                "color": _FORMULA_COLOR,
                "anchor": "edge_up",
                "shift": [0, 0],
            },
        )
    )
    project.scene.objects.append(
        SceneObject(
            id=dest_id,
            name="Transformed",
            type="math_formula",
            params={
                "latex": _DEST_LATEX,
                "color": _FORMULA_COLOR,
                "anchor": "edge_up",
                "shift": [0, 0],
            },
        )
    )
    _insert_beats(
        project,
        [
            Beat(id=write_id, duration=2, action="write", targets=[source_id]),
            Beat(
                id=transform_id,
                duration=2,
                action="transform_formula",
                targets=[source_id, dest_id],
            ),
        ],
        after_beat_id,
    )
    project.scene.template_instances.append(
        TemplateInstance(
            id=template_id,
            template_id="formula_transform",
            params={
                "sourceLatex": _SOURCE_LATEX,
                "destinationLatex": _DEST_LATEX,
                "sourceColor": _FORMULA_COLOR,
                "destinationColor": _FORMULA_COLOR,
                "anchor": "edge_up",
                "shift": [0, 0],
            },
            object_ids=[source_id, dest_id],
            beat_ids=[write_id, transform_id],
        )
    )
    return transform_id, dest_id


def add_three_d_axes(project: Project, _after_beat_id: str | None) -> tuple[str, str]:
    """Insert the Cairo 3D template into an empty project.

    Returns the surface beat id and the surface object id. The project must
    have no objects and no beats.
    """
    if (
        project.scene.kind != "scene"
        or project.scene.objects
        or project.scene.beats
        or project.scene.template_instances
    ):
        raise DocumentError("A 3D template needs an empty project.")
    used = used_ids(project)
    axes_id = fresh_id("axes", used)
    surface_id = fresh_id("surface", used)
    axes_beat_id = fresh_id("beat", used)
    surface_beat_id = fresh_id("beat", used)
    template_id = fresh_id("tmpl", used)
    x_range = list(_THREE_D_X_RANGE)
    y_range = list(_THREE_D_Y_RANGE)
    z_range = list(_THREE_D_Z_RANGE)
    u_range = list(_SURFACE_RANGE)
    v_range = list(_SURFACE_RANGE)
    resolution = list(_SURFACE_RESOLUTION)
    components = list(_CURVE_COMPONENTS)
    t_range = list(_CURVE_RANGE)
    project.scene.kind = "three_d_scene"
    project.scene.camera = Camera(phi=_CAMERA_PHI, theta=_CAMERA_THETA, zoom=_CAMERA_ZOOM)
    project.scene.objects.append(
        SceneObject(
            id=axes_id,
            name="Axes",
            type="three_d_axes",
            params={"xRange": x_range, "yRange": y_range, "zRange": z_range},
        )
    )
    project.scene.objects.append(
        SceneObject(
            id=surface_id,
            name="Surface",
            type="surface",
            params={
                "axesId": axes_id,
                "expression": _SURFACE_EXPRESSION,
                "uRange": u_range,
                "vRange": v_range,
                "resolution": resolution,
                "color": _SURFACE_COLOR,
            },
        )
    )
    project.scene.beats.extend(
        [
            Beat(id=axes_beat_id, duration=2, action="create", targets=[axes_id]),
            Beat(id=surface_beat_id, duration=2, action="create", targets=[surface_id]),
        ]
    )
    project.scene.template_instances.append(
        TemplateInstance(
            id=template_id,
            template_id="three_d_axes",
            params={
                "mode": "surface",
                "xRange": list(x_range),
                "yRange": list(y_range),
                "zRange": list(z_range),
                "phi": _CAMERA_PHI,
                "theta": _CAMERA_THETA,
                "zoom": _CAMERA_ZOOM,
                "expression": _SURFACE_EXPRESSION,
                "uRange": list(u_range),
                "vRange": list(v_range),
                "resolution": list(resolution),
                "surfaceColor": _SURFACE_COLOR,
                "components": components,
                "tRange": t_range,
                "curveColor": _CURVE_COLOR,
            },
            object_ids=[axes_id, surface_id],
            beat_ids=[axes_beat_id, surface_beat_id],
        )
    )
    return surface_beat_id, surface_id


def add_formula(
    project: Project,
    after_beat_id: str | None,
    latex: str = _DEFAULT_LATEX,
) -> tuple[str, str]:
    """Insert a formula whose entrance is ``write``. Returns beat id and object id."""
    _reject_3d(project)
    if len(latex) > 500:
        raise DocumentError("latex is longer than 500 characters")
    used = used_ids(project)
    formula_id = fresh_id("formula", used)
    beat_id = fresh_id("beat", used)
    project.scene.objects.append(
        SceneObject(
            id=formula_id,
            name="Formula",
            type="math_formula",
            params={
                "latex": latex,
                "color": "#FFFFFF",
                "anchor": "edge_up",
                "shift": [0, 0],
            },
        )
    )
    _insert_beats(
        project,
        [Beat(id=beat_id, duration=2, action="write", targets=[formula_id])],
        after_beat_id,
    )
    return beat_id, formula_id


def add_text(
    project: Project,
    after_beat_id: str | None,
    text: str = "Text",
) -> tuple[str, str]:
    """Insert a text object whose entrance is ``write``."""
    _reject_3d(project)
    if len(text) > 200:
        raise DocumentError("text is longer than 200 characters")
    used = used_ids(project)
    text_id = fresh_id("text", used)
    beat_id = fresh_id("beat", used)
    project.scene.objects.append(
        SceneObject(
            id=text_id,
            name="Text",
            type="text",
            params={
                "text": text,
                "color": "#FFFFFF",
                "anchor": "edge_up",
                "shift": [0, 0],
            },
        )
    )
    _insert_beats(
        project,
        [Beat(id=beat_id, duration=2, action="write", targets=[text_id])],
        after_beat_id,
    )
    return beat_id, text_id


def assert_plot_expression(expression: str) -> None:
    assert_supported(expression, frozenset({"x"}))


def _insert_beats(project: Project, beats: list[Beat], after_beat_id: str | None) -> None:
    if after_beat_id is None:
        project.scene.beats.extend(beats)
        return
    beat_by_id(project, after_beat_id)
    for index, beat in enumerate(project.scene.beats):
        if beat.id == after_beat_id:
            project.scene.beats[index + 1 : index + 1] = beats
            return


def _reject_3d(project: Project) -> None:
    if project.scene.kind != "scene":
        raise DocumentError("A 3D scene only holds the 3D template.")
