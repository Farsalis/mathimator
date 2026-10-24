"""The supervisor's pure parts, without a supervisor: the project layout, the preview index and export publishing (F088)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mathimator_worker.argv import MEDIA_DIR, SCENE_FILE
from mathimator_worker.document import FORMATS, Project, load_text
from mathimator_worker.exports import PublishFailed, _export_problem, existing_export, publish_export
from mathimator_worker.identity import beat_chain_keys, export_folder_name, export_key
from mathimator_worker.layout import ProjectLayout
from mathimator_worker.preview_index import IndexBeat, PreviewIndex, rebuild_index
from tests.support import REPO

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"


def test_the_layout_is_the_folder_tree_of_spec_section_4(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    project = _journey()
    paths = {
        "project_file": layout.project_file,
        "scene_path": layout.scene_path,
        "media_dir": layout.media_dir,
        "last_good_dir": layout.last_good_dir,
        "rendered_path": layout.rendered_path,
        "index_path": layout.index_path,
        "run_marker": layout.run_marker,
        "log_path": layout.log_path,
        "export_dir": layout.export_dir(project),
        "video_dir": layout.video_dir("480p15"),
        "partial_movie_dir": layout.partial_movie_dir("1080p30"),
        "png_frames_dir": layout.png_frames_dir,
    }
    assert {name: path.relative_to(tmp_path).as_posix() for name, path in paths.items()} == {
        "project_file": "project.json",
        "scene_path": ".mathimator/codegen/scene.py",
        "media_dir": ".mathimator/cache/media",
        "last_good_dir": ".mathimator/cache/last-good",
        "rendered_path": ".mathimator/cache/rendered.json",
        "index_path": ".mathimator/cache/preview-index.json",
        "run_marker": ".mathimator/cache/manim-run.json",
        "log_path": ".mathimator/logs/worker.log",
        "export_dir": f".mathimator/exports/{export_folder_name(project)}",
        "video_dir": ".mathimator/cache/media/videos/scene/480p15",
        "partial_movie_dir": ".mathimator/cache/media/videos/scene/1080p30/partial_movie_files/MainScene",
        "png_frames_dir": ".mathimator/cache/media/images/scene/MainScene",
    }
    assert [path.name for path in layout.derived_dirs] == ["cache", "codegen", "equation", "typst"]


def test_the_layout_watches_the_files_manim_is_given(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    # Manim runs in the codegen folder, so its scene file and media folder are relative to it.
    runs_in = layout.scene_path.parent
    assert runs_in / SCENE_FILE == layout.scene_path
    assert (runs_in / MEDIA_DIR).resolve() == layout.media_dir.resolve()


# The preview index


def test_a_rebuilt_index_keeps_only_files_rendered_for_each_beat_as_it_is() -> None:
    project = _journey()
    keys = beat_chain_keys(project, "preview")
    cut_off = {"b3-cut.mp4"}
    previous = PreviewIndex(
        output="movie.mp4",
        beats=[
            _indexed("beat_1", 0, partial=("b1.mp4", keys[0]), last_good=("b1-old.mp4", "older")),
            _indexed("beat_2", 1, partial=("b2.mp4", "another document"), last_good=("b2-old.mp4", "older")),
            _indexed("beat_3", 2, partial=("b3-cut.mp4", keys[2])),
        ],
    )
    index = rebuild_index(previous, project.scene.beats, keys, lambda path: path.name not in cut_off, lambda: True)
    assert [(beat.id, beat.index, beat.derived_start, beat.duration) for beat in index.beats] == [
        ("beat_1", 0, 0.0, 2.0),
        ("beat_2", 1, 2.0, 3.0),
        ("beat_3", 2, 5.0, 2.0),
        ("beat_4", 3, 7.0, 1.0),
    ]
    stored = [(beat.fresh, beat.partial, beat.partial_key, beat.last_good, beat.last_good_key) for beat in index.beats]
    assert stored == [
        (True, "b1.mp4", keys[0], None, None),
        # The newest file stays as last good, under the badge.
        (False, None, None, "b2.mp4", "another document"),
        # A cut-off clip never counts (C14 Q3).
        (False, None, None, None, None),
        (False, None, None, None, None),
    ]
    # The movie stays only while every beat is fresh.
    assert index.output is None


@pytest.mark.parametrize(
    ("current", "complete", "kept"), [(True, True, True), (False, True, False), (True, False, False)]
)
def test_the_full_movie_stays_only_while_it_is_the_render_of_this_document(
    current: bool, complete: bool, kept: bool
) -> None:
    project = _journey()
    keys = beat_chain_keys(project, "preview")
    beats = [
        _indexed(beat.id, index, partial=(f"{beat.id}.mp4", keys[index]))
        for index, beat in enumerate(project.scene.beats)
    ]
    previous = PreviewIndex(output="movie.mp4", beats=beats)
    index = rebuild_index(
        previous, project.scene.beats, keys, lambda path: complete or path.name != "movie.mp4", lambda: current
    )
    assert all(beat.fresh for beat in index.beats)
    assert index.output == ("movie.mp4" if kept else None)


def test_recording_a_partial_makes_its_beat_fresh() -> None:
    index = PreviewIndex(output=None, beats=[_indexed("beat_1", 0, last_good=("old.mp4", "older"))])
    index.record([(0, Path("new.mp4")), (5, Path("beyond.mp4"))], ["key-0"])
    (beat,) = index.beats
    assert (beat.fresh, beat.partial, beat.partial_key, beat.last_good, beat.last_good_key) == (
        True,
        "new.mp4",
        "key-0",
        None,
        None,
    )


# Exports


def test_a_finished_export_is_published_with_its_manifest_and_then_reused(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    project = _journey()
    assert existing_export(layout, project) is None
    movie = _file(layout.video_dir("1080p30") / "MainScene.mp4", b"movie")
    published = publish_export(layout, project, movie, "1080p30")
    assert published == layout.export_dir(project) / "scene.mp4"
    assert published.read_bytes() == b"movie"
    manifest = json.loads((published.parent / "export.json").read_text(encoding="utf-8"))
    assert (manifest["exportKey"], manifest["file"]) == (export_key(project), "scene.mp4")
    assert existing_export(layout, project) == published
    (published.parent / "export.json").write_text(json.dumps({"exportKey": "another"}), encoding="utf-8")
    assert existing_export(layout, project) is None


def test_the_preview_is_never_published(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    project = _journey()
    preview = _file(layout.video_dir("480p15") / "MainScene.mp4", b"preview")
    with pytest.raises(PublishFailed, match="the render is the preview"):
        publish_export(layout, project, preview, "1080p30")
    assert not layout.exports_dir.exists()


@pytest.mark.parametrize(
    ("relative", "fmt", "problem"),
    [
        ("videos/scene/1080p30/MainScene.mp4", "mp4", None),
        ("videos/scene/1080p30/MainScene.mp4", "webm", "the render is not a WEBM file"),
        ("videos/scene/720p30/MainScene.mp4", "mp4", "the render is the preview"),
        ("videos/scene/480p15/MainScene.gif", "gif", "the render is the preview"),
        ("../outside/MainScene.mp4", "mp4", "the render is not in the project's media folder"),
        ("images/scene/MainScene", "png_sequence", None),
        ("images/scene/empty", "png_sequence", "the render holds no PNG frames"),
    ],
)
def test_the_preview_file_guard(tmp_path: Path, relative: str, fmt: str, problem: str | None) -> None:
    media = ProjectLayout(tmp_path).media_dir
    _file(media / "images" / "scene" / "MainScene" / "0000.png", b"png")
    (media / "images" / "scene" / "empty").mkdir()
    movie = media / relative
    if movie.suffix:
        _file(movie, b"movie")
    target = tmp_path / "exports" / FORMATS[fmt].artifact_name
    assert _export_problem(movie, target, fmt, media, "1080p30") == problem


def _journey() -> Project:
    return load_text(JOURNEY.read_text(encoding="utf-8"))


def _indexed(
    beat_id: str,
    index: int,
    *,
    partial: tuple[str, str] | None = None,
    last_good: tuple[str, str] | None = None,
) -> IndexBeat:
    partial_path, partial_key = partial if partial is not None else (None, None)
    last_good_path, last_good_key = last_good if last_good is not None else (None, None)
    return IndexBeat(
        id=beat_id,
        index=index,
        derived_start=0.0,
        duration=0.0,
        partial=partial_path,
        last_good=last_good_path,
        fresh=False,
        partial_key=partial_key,
        last_good_key=last_good_key,
    )


def _file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
