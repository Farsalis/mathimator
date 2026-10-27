"""Documents written before the beat catalog keep their bytes, their scene.py and their cache keys.

``tests/fixtures/beats/legacy-goldens.json`` was recorded from the code before
more beats, shapes, re-entry and easing came in. A document that uses none of
them must save, compile and hash exactly as it did then: the same
``project.json`` bytes, the same ``scene.py``, the same beat and chain keys in
both profiles, the same ``sceneHash`` and ``exportKey``, so no existing preview
or export is thrown away. The file is a record of the past and is never
rewritten; ``write_goldens`` only shows how it was made.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mathimator_worker.codegen import generate
from mathimator_worker.document import Project, dumps_project, empty_project, load_text
from mathimator_worker.identity import beat_cache_keys, beat_chain_keys, export_key, scene_hash
from mathimator_worker.jsonio import JsonValue
from mathimator_worker.session import Session
from tests.support import REPO

GOLDENS = REPO / "tests" / "fixtures" / "beats" / "legacy-goldens.json"
FIXTURES = ("journey-a", "reference-60", "lesson-60", "fractional-10")
# Template insertions into a new project, as the Add menu makes them.
INSERTIONS: dict[str, list[dict[str, object]]] = {
    "axes-function": [{"op": "add_axes_function", "afterBeatId": None}],
    "formula-transform": [{"op": "add_formula_transform", "afterBeatId": None}],
    "surface": [{"op": "add_three_d_axes", "afterBeatId": None}],
    "curve": [{"op": "add_three_d_axes", "afterBeatId": None}, {"op": "set_three_d_mode", "mode": "curve"}],
}
CASES = (*FIXTURES, *INSERTIONS)


@pytest.mark.parametrize("name", CASES)
def test_a_legacy_document_is_unchanged(name: str) -> None:
    expected = json.loads(GOLDENS.read_text(encoding="utf-8"))[name]
    assert record(document(name)) == expected


def test_every_case_is_recorded() -> None:
    assert list(json.loads(GOLDENS.read_text(encoding="utf-8"))) == list(CASES)


def document(name: str) -> Project:
    if name in INSERTIONS:
        session = Session(empty_project())
        for edit in INSERTIONS[name]:
            session.edit(edit)
        return session.project
    return load_text((REPO / "tests" / "fixtures" / name / "project.json").read_text(encoding="utf-8"))


def record(project: Project) -> dict[str, JsonValue]:
    """Everything a legacy document must keep."""
    return {
        "projectJson": dumps_project(project),
        "sceneSource": generate(project),
        "previewKeys": list[JsonValue](beat_cache_keys(project, "preview")),
        "exportKeys": list[JsonValue](beat_cache_keys(project, "export")),
        "previewChainKeys": list[JsonValue](beat_chain_keys(project, "preview")),
        "exportChainKeys": list[JsonValue](beat_chain_keys(project, "export")),
        "sceneHash": scene_hash(project),
        "exportKey": export_key(project),
    }


def write_goldens(path: Path = GOLDENS) -> None:
    """How the record was made, once, before the change it guards."""
    payload = {name: record(document(name)) for name in CASES}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
