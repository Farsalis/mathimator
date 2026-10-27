"""What is on screen before each beat, and the camera in effect: the timeline rules of spec section 4.

Validation plays the beats out in order. Nothing is on screen at the start. An
entrance needs its targets off screen and brings them on; an exit needs them
on and takes them off; a change or an emphasis needs them on and leaves them
on; a morph takes its source off and brings its destination on. An object may
leave and come back any number of times, and every object enters at least
once. An object drawn on axes is on screen only while its axes are. The first
broken rule is refused with a message that names the beat and the object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from mathimator_worker.beats import AXES_CHILD_TYPES, EASINGS, KINDS, BeatKind, State, effective_easing, states
from mathimator_worker.document_error import DocumentError

if TYPE_CHECKING:
    from mathimator_worker.document import Beat, Camera, Number, Project, SceneObject


@dataclass(frozen=True)
class Timeline:
    # The ids on screen before each beat, then after the last, in document order.
    on_screen: list[list[str]]
    # The camera in effect before each beat, then after the last; None in a 2D scene.
    cameras: list[Camera | None]


class TimelineError(DocumentError):
    """A broken on-screen rule, and the object at fault.

    ``beat_index`` is the beat that breaks the rule, and leaving ``target``
    out of it would not; None means that ``target`` never comes on screen.
    ``delete_object`` repairs a timeline that lost an object this way.
    """

    def __init__(self, message: str, *, field: str, beat_index: int | None, target: str) -> None:
        super().__init__(message, field=field)
        self.beat_index = beat_index
        self.target = target


@dataclass(frozen=True)
class _Exit:
    beat_index: int
    # The destination of the morph that took the object off screen, if one did.
    destination: str | None


def simulate(project: Project) -> Timeline:
    """Play the beats out on screen. Raises DocumentError for the first broken rule.

    A broken rule about what is on screen, including an object that never
    enters, is a TimelineError. ``project`` is parsed, and the objects drawn
    on axes name axes of the right type, as ``document`` checks before it
    calls this.
    """
    scene = project.scene
    objects = {item.id: item for item in scene.objects}
    on: set[str] = set()
    entered: set[str] = set()
    exits: dict[str, _Exit] = {}
    camera = scene.camera
    on_screen: list[list[str]] = []
    cameras: list[Camera | None] = []
    for index, beat in enumerate(scene.beats):
        on_screen.append(_listed(scene.objects, on))
        cameras.append(camera)
        path = f"scene.beats[{index}]"
        kind = KINDS[beat.action]
        _check_beat(kind, beat, objects, scene.kind, path)
        changes = list(zip(beat.targets, _verbs(kind, len(beat.targets)), states(kind, len(beat.targets)), strict=True))
        for target, verb, (before, _after) in changes:
            _check_state(target, verb, before, on, entered, exits, index)
        came: set[str] = set()
        left: set[str] = set()
        for target, _verb, (before, after) in changes:
            if before == after:
                continue
            if after == "on":
                on.add(target)
                entered.add(target)
                came.add(target)
            else:
                on.discard(target)
                left.add(target)
                exits[target] = _Exit(index, beat.targets[1] if kind.family == "morph" else None)
        _check_axes(scene.objects, on, came, left, index)
        camera = _camera_after(kind, beat, camera)
    on_screen.append(_listed(scene.objects, on))
    cameras.append(camera)
    for index, item in enumerate(scene.objects):
        if item.id not in entered:
            path = f"scene.objects[{index}]"
            raise TimelineError(f"{path} {item.id} has no entrance beat", field=path, beat_index=None, target=item.id)
    return Timeline(on_screen, cameras)


def canonical_targets(project: Project, action: str, targets: Sequence[str]) -> list[str]:
    """The order a beat edit stores the targets of ``action`` in.

    ``tests/fixtures/beats/canonical-order.json`` pins this rule for the
    worker and for the UI's copy of it.

    The targets of a kind of arity "some" go in document order, then each axes
    that comes after one of its own children moves to just before the first of
    them, so axes always come before what is drawn on them. A pair keeps its
    role order. Ids that name no object keep their order at the end.
    """
    ordered = list(targets)
    if KINDS[action].arity != "some":
        return ordered
    objects = project.scene.objects
    position = {item.id: index for index, item in enumerate(objects)}
    ordered.sort(key=lambda target: position.get(target, len(objects)))
    axes_of = {item.id: item.params.get("axesId") for item in objects if item.type in AXES_CHILD_TYPES}
    for axes_id in [target for target in ordered if target in axes_of.values()]:
        children = [index for index, target in enumerate(ordered) if axes_of.get(target) == axes_id]
        if children and children[0] < ordered.index(axes_id):
            ordered.remove(axes_id)
            ordered.insert(children[0], axes_id)
    return ordered


def scene_refusal(kind: BeatKind, scene_kind: str, path: str) -> str | None:
    """Why the beat at ``path`` cannot be of ``kind`` in a scene of ``scene_kind``, or None when it can."""
    if scene_kind in kind.scenes:
        return None
    where = "is not available in a 3D scene" if scene_kind == "three_d_scene" else "is only for a 3D scene"
    return f"{path}.action {kind.action} {where}"


def _listed(objects: Sequence[SceneObject], on: set[str]) -> list[str]:
    return [item.id for item in objects if item.id in on]


def _verbs(kind: BeatKind, count: int) -> list[str]:
    """The verb of a refusal that names each target."""
    if kind.arity == "pair":
        return [role.verb for role in kind.roles[:count]]
    return [kind.verb] * count


def _check_beat(kind: BeatKind, beat: Beat, objects: Mapping[str, SceneObject], scene_kind: str, path: str) -> None:
    """The rules that do not depend on what is on screen: scene kind, count, names, repeats and types."""
    field = f"{path}.targets"
    refusal = scene_refusal(kind, scene_kind, path)
    if refusal is not None:
        raise DocumentError(refusal, field=f"{path}.action")
    count = len(beat.targets)
    if kind.arity == "none" and count:
        raise DocumentError(f"{field} must be empty for {kind.action}", field=field)
    if kind.arity == "some" and not count:
        raise DocumentError(f"{field} must name at least one object for {kind.action}", field=field)
    if kind.arity == "pair" and count != len(kind.roles):
        nouns = " and ".join(role.noun for role in kind.roles)
        raise DocumentError(f"{field} must be {nouns}", field=field)
    for target in beat.targets:
        if target not in objects:
            raise DocumentError(f"{field} names {target}, which is not an object", field=field)
    seen: set[str] = set()
    for target in beat.targets:
        if target in seen:
            raise DocumentError(f"{field} names {target} twice", field=field)
        seen.add(target)
    for position, target in enumerate(beat.targets):
        refusal = _type_refusal(kind, position, target, objects[target].type, path)
        if refusal is not None:
            raise DocumentError(refusal, field=field)


def _type_refusal(kind: BeatKind, position: int, target: str, item_type: str, path: str) -> str | None:
    """Why the target at ``position`` cannot take ``kind``, or None when its type can."""
    if kind.arity == "pair":
        role = kind.roles[position]
        if item_type in role.types:
            return None
        generic = f"{path} {role.verb} {target}, but the {role.label.lower()} of {kind.action} cannot be {item_type}"
    elif item_type in kind.types:
        return None
    else:
        generic = f"{path} {kind.verb} {target}, but {kind.action} does not apply to {item_type}"
    if kind.type_refusal is not None:
        return kind.type_refusal.format(path=path, id=target)
    return generic


def _check_state(
    target: str, verb: str, before: State, on: set[str], entered: set[str], exits: Mapping[str, _Exit], index: int
) -> None:
    """The target is off screen for a beat that brings it on, and on screen for every other beat."""
    path = f"scene.beats[{index}]"
    if before == "off":
        if target not in on:
            return
        message = f"{path} brings {target} on screen while it is already on screen"
    elif target in on:
        return
    elif target not in entered:
        message = f"{path} {verb} {target} before it is on screen"
    else:
        gone = exits[target]
        if gone.destination is not None:
            # Manim would bring a transformed formula back (D09, F012), so the wording names the transform.
            reason = f"when it was transformed into {gone.destination}"
        else:
            reason = f"at scene.beats[{gone.beat_index}]"
        message = f"{path} {verb} {target}, but {target} left the screen {reason}"
    raise TimelineError(message, field=f"{path}.targets", beat_index=index, target=target)


def _check_axes(objects: Sequence[SceneObject], on: set[str], came: set[str], left: set[str], index: int) -> None:
    """After the beat, everything on screen that is drawn on axes has its axes on screen (F011).

    When the axes left in this beat, keeping them on is the repair; otherwise
    the object drawn on them came on while they were off, and keeping it off is.
    """
    path = f"scene.beats[{index}]"
    for item in objects:
        axes_id = item.params.get("axesId")
        if item.id not in on or item.type not in AXES_CHILD_TYPES or not isinstance(axes_id, str) or axes_id in on:
            continue
        if axes_id in left and item.id in came:
            message = f"{path} transforms {axes_id} into {item.id}, but {item.id} is drawn on {axes_id}"
        elif axes_id in left:
            message = f"{path} takes {axes_id} off screen while {item.id}, which is drawn on it, is still on screen"
        else:
            message = f"{path} brings {item.id} on screen before its axes {axes_id}"
        target = axes_id if axes_id in left else item.id
        raise TimelineError(message, field=f"{path}.targets", beat_index=index, target=target)


def _camera_after(kind: BeatKind, beat: Beat, camera: Camera | None) -> Camera | None:
    """A camera move leaves the camera at its params, unless its easing comes back to where it started."""
    if kind.family != "camera" or camera is None or beat.params is None:
        return camera
    easing = effective_easing(kind, beat.easing)
    if easing is None or EASINGS[easing].ends_at == 0:
        return camera
    params = beat.params
    return replace(camera, phi=_number(params["phi"]), theta=_number(params["theta"]), zoom=_number(params["zoom"]))


def _number(value: object) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError("a camera setting must be a number")
    return value
