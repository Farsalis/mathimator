"""The projects of the quick render smoke tests are current, load, save as written and compile.

``tests/smoke_projects.py`` builds them and says what each is for;
``uv run pytest tests/test_smoke_projects.py --update-golden`` rewrites
``tests/fixtures/render-smoke``. No test here starts Manim: the render tier
renders them.
"""

from __future__ import annotations

import pytest

from mathimator_worker.beats import EASINGS, KINDS, SHAPES
from mathimator_worker.codegen import generate
from mathimator_worker.document import Project, load_text
from tests.smoke_projects import CACHE_SCENES, FOLDER, SECONDS, UNSTABLE_KINDS, project_text, projects

PROJECTS = projects()


def read(name: str) -> Project:
    return load_text((FOLDER / name / "project.json").read_text(encoding="utf-8"))


def test_the_projects_are_current(pytestconfig: pytest.Config) -> None:
    built = {name: project_text(raw) for name, raw in PROJECTS.items()}
    if pytestconfig.getoption("update_golden"):
        for name, text in built.items():
            (FOLDER / name).mkdir(parents=True, exist_ok=True)
            (FOLDER / name / "project.json").write_text(text, encoding="utf-8", newline="\n")
        return
    stale = "render-smoke is out of date: rewrite it with uv run pytest tests/test_smoke_projects.py --update-golden"
    assert sorted(path.name for path in FOLDER.iterdir()) == sorted(built), stale
    for name, text in built.items():
        assert (FOLDER / name / "project.json").read_text(encoding="utf-8") == text, (stale, name)


@pytest.mark.parametrize("name", PROJECTS)
def test_each_project_compiles_with_no_group_played(name: str) -> None:
    source = generate(read(name))
    compile(source, f"{name}/scene.py", "exec")
    assert not [line for line in source.splitlines() if "self.play(" in line and "VGroup(" in line]


def test_every_kind_and_every_shape_has_a_scene_of_its_own() -> None:
    for action in KINDS:
        assert action in {beat.action for beat in read(f"kind-{action}").scene.beats}
    for shape_type in SHAPES:
        assert shape_type in {item.type for item in read(f"shape-{shape_type}").scene.objects}


def test_every_kind_that_takes_an_arrow_acts_on_the_arrow() -> None:
    takes = {
        action
        for action, kind in KINDS.items()
        if "arrow" in kind.types or any("arrow" in role.types for role in kind.roles)
    }
    assert takes <= {beat.action for beat in read("arrow").scene.beats if "arrow_1" in beat.targets}


def test_every_easing_eases_a_play_and_a_camera_move() -> None:
    assert [beat.easing for beat in read("easings").scene.beats if beat.action == "scale"] == list(EASINGS)
    camera = [beat.easing for beat in read("three-d-curve").scene.beats if beat.action == "move_camera"]
    assert camera == list(EASINGS)


def test_the_3d_scenes_use_every_kind_a_3d_scene_offers() -> None:
    offered = {action for action, kind in KINDS.items() if "three_d_scene" in kind.scenes}
    assert offered == {beat.action for beat in read("three-d-curve").scene.beats}
    assert read("three-d-surface").scene.template_instances[0].params["mode"] == "surface"


def test_the_cache_scenes_play_every_unstable_kind_with_a_play_after_it() -> None:
    followed = {beat.action for name in CACHE_SCENES for beat in read(name).scene.beats[:-1]}
    assert UNSTABLE_KINDS - followed == set()


@pytest.mark.parametrize("name", PROJECTS)
def test_every_beat_lasts_half_a_second(name: str) -> None:
    assert {beat.duration for beat in read(name).scene.beats} == {SECONDS}
