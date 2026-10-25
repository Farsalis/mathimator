"""The cache identity of a document: what a partial movie or an export was rendered from (spec section 4).

Each identity is the SHA-256 of a canonical JSON payload. Display names are
left out, so a rename neither dirties a preview nor throws away an export.
Changing a payload changes every key and throws the whole cache away, so the
payloads change only with ``GENERATOR_VERSION``.
"""

from __future__ import annotations

import hashlib

from mathimator_worker.beats import AXES_CHILD_TYPES
from mathimator_worker.document import (
    MANIM_VERSION,
    Beat,
    ProfileName,
    Project,
    SceneObject,
    object_dict,
    project_dict,
)
from mathimator_worker.jsonio import JsonValue, canonical_dumps


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


def beat_cache_keys(project: Project, profile: ProfileName) -> list[str]:
    """Each beat's key in ``profile``: the beat, the objects it draws, the profile, the generator and the seed."""
    objects = {item.id: item for item in project.scene.objects}
    rendered = _profile_payload(project, profile)
    return [
        _sha256(canonical_dumps(_beat_cache_payload(project, index, objects, rendered)))
        for index in range(len(project.scene.beats))
    ]


def beat_chain_keys(project: Project, profile: ProfileName) -> list[str]:
    """Each beat's identity together with everything before it: the scene stamp and the keys of beats 0 to i.

    A partial movie shows the scene as every earlier beat left it, so it
    belongs to a document only while this key is unchanged (F135).
    """
    chain = scene_stamp(project)
    keys: list[str] = []
    for key in beat_cache_keys(project, profile):
        chain = _sha256(chain + key)
        keys.append(chain)
    return keys


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
    """The export's folder in ``exports/``: the first 16 hex digits of its key."""
    return export_key(project)[:16]


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


def _profile_payload(project: Project, profile: ProfileName) -> dict[str, JsonValue]:
    if profile == "preview":
        preview = project.preview_profile
        return {
            "fps": preview.fps,
            "height": preview.height,
            "transparent": preview.transparent,
            "width": preview.width,
        }
    export = project.export_profile
    return {
        "format": export.format,
        "fps": export.fps,
        "height": export.height,
        "transparent": export.transparent,
        "width": export.width,
    }


def _beat_cache_payload(
    project: Project,
    index: int,
    objects: dict[str, SceneObject],
    profile: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    beat = project.scene.beats[index]
    beat_payload: dict[str, JsonValue] = {
        "action": beat.action,
        "duration": beat.duration,
        "id": beat.id,
        "targets": list(beat.targets),
    }
    if beat.params is not None:
        beat_payload["params"] = dict(beat.params)
    # Only when named, so the keys of a beat on its default easing stay as they were.
    if beat.easing is not None:
        beat_payload["easing"] = beat.easing
    return {
        "beat": beat_payload,
        "beatIndex": index,
        "generatorVersion": project.generator_version,
        "manimVersion": MANIM_VERSION,
        "objects": _objects_for_beat(objects, beat),
        "profile": profile,
        "seed": project.seed,
    }


def _objects_for_beat(objects: dict[str, SceneObject], beat: Beat) -> list[JsonValue]:
    """The objects ``beat`` draws, each after the axes it is drawn on."""
    if beat.action == "wait":
        return []
    ordered: list[JsonValue] = []
    seen: set[str] = set()

    def add(object_id: str) -> None:
        if object_id in seen:
            return
        item = objects[object_id]
        if item.type in AXES_CHILD_TYPES:
            axes_id = item.params.get("axesId")
            if isinstance(axes_id, str):
                add(axes_id)
        seen.add(object_id)
        ordered.append(_object_without_name(item))

    for target in beat.targets:
        add(target)
    return ordered


def _object_without_name(item: SceneObject) -> dict[str, JsonValue]:
    payload = object_dict(item)
    payload.pop("name", None)
    return payload


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
