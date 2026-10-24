"""How Manim and LaTeX are run (contract C7.2 job start; fix-plan S39, S40, S42; D18, D31, D60, D61, C14 Q4).

The command line, the folder and environment Manim gets, the TeX folder it
finds, the segment list FFmpeg reads, and the checks a job passes before
Manim starts: codegen, TeX and the project path. Jobs run the ``fake_manim``
stand-in; only the render-tier test starts Manim.
"""

from __future__ import annotations

import configparser
import inspect
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from mathimator_worker import manim_paths
from mathimator_worker import supervisor as supervisor_module
from mathimator_worker.argv import CONFIG_FILE, build_manim_argv, manim_environment
from mathimator_worker.codegen import CodegenError, needs_latex
from mathimator_worker.document import MAX_TOTAL_SECONDS, MIN_BEAT_SECONDS, Project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.exports import existing_export
from mathimator_worker.identity import export_folder_name, export_key
from mathimator_worker.manim_main import concat_manifest_bytes
from mathimator_worker.manim_paths import PARTIAL_FILE, TEX_FILE, longest_folder, manim_root, short_path
from mathimator_worker.progress import read_manifest
from mathimator_worker.supervisor import Supervisor, _Job, _remove_tex_inputs
from tests.support import (
    REFERENCE,
    REPO,
    FakeManim,
    Stdout,
    complete_clip,
    handwritten_project,
    start_supervisor,
    video_render,
    write_clip,
)

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"
# The render tier leaves its projects here.
RENDERS = REPO / "out" / "manim-run"
# The name Manim gives a TeX file: the first 16 digits of a SHA-256.
TEX_HASH = "0123456789abcdef"
TEX_ERROR = "Formulas and axis labels cannot render on this PC: ! I can't find file `check'. Reinstall Mathimator."
CODEGEN_ERROR = "eq_1 left the screen when it was transformed into eq_2"
# A short folder for the path checks, which read no file.
LESSONS = Path("C:/lessons")
_PROFILES = (
    ("preview", "mp4", False),
    ("export", "mp4", False),
    ("export", "gif", False),
    ("export", "webm", True),
    ("export", "png_sequence", True),
)

# The command line and Manim's settings (D60, F124, F170, G2.1, G2.2)


@pytest.mark.parametrize(("profile", "fmt", "transparent"), _PROFILES)
def test_every_render_runs_isolated_and_offline(profile: str, fmt: str, transparent: bool) -> None:
    argv = build_manim_argv("python", profile, fmt=fmt, transparent=transparent)
    # Python ignores the folder it runs in and checks planted bytecode against its source.
    assert argv[:6] == ["python", "-P", "--check-hash-based-pycs", "always", "-m", "mathimator_worker.manim_main"]
    assert argv[6:9] == ["render", "scene.py", "MainScene"]
    assert argv[argv.index("--config_file") + 1] == str(CONFIG_FILE)
    # --silent keeps Manim from asking PyPI for a newer release after every render.
    assert "--silent" in argv
    assert argv[argv.index("--media_dir") + 1] == "../cache/media"


def test_manim_keeps_every_clip_of_the_longest_document() -> None:
    parser = configparser.ConfigParser()
    assert parser.read(CONFIG_FILE, encoding="utf-8") == [str(CONFIG_FILE)]
    assert parser["CLI"].getint("max_files_cached") >= MAX_TOTAL_SECONDS / MIN_BEAT_SECONDS


def test_manim_gets_its_own_home_and_latex_runs_no_shell_command(tmp_path: Path) -> None:
    user = {
        "PATH": "C:\\TinyTeX\\bin",
        "USERPROFILE": "C:\\Users\\someone",
        "HOME": "/home/someone",
        "SHELL_ESCAPE": "t",
        "Openout_Any": "a",
        # kpathsea reads these for latex before the plain names.
        "shell_escape_latex": "t",
        "Openout_Any.latex": "a",
    }
    environment = manim_environment(user, tmp_path)
    assert environment["PATH"] == "C:\\TinyTeX\\bin"
    assert (environment["HOME"], environment["USERPROFILE"]) == (str(tmp_path), str(tmp_path))
    kpathsea = ("SHELL_ESCAPE", "OPENIN_ANY", "OPENOUT_ANY")
    tex = {name: value for name, value in environment.items() if name.upper().startswith(kpathsea)}
    assert tex == {"shell_escape": "f", "openin_any": "p", "openout_any": "p"}
    # Windows reads names in any case, so no variant of a setting may stay beside it.
    names = [name.upper() for name in environment]
    assert len(names) == len(set(names))
    assert environment["PYTHONUTF8"] == "1"


# The segment list FFmpeg reads (D61, G4.3)


def test_the_segment_list_quotes_an_apostrophe_for_ffmpeg(tmp_path: Path) -> None:
    folder = "C:/Users/O'Neil/Mr O'Neil's lesson/.mathimator/cache/media"
    listing = concat_manifest_bytes([f"{folder}/a.mp4", f"{folder}/it's.mp4"]).decode("utf-8")
    assert listing.splitlines()[1:] == [
        "file 'file:C:/Users/O'\\''Neil/Mr O'\\''Neil'\\''s lesson/.mathimator/cache/media/a.mp4'",
        "file 'file:C:/Users/O'\\''Neil/Mr O'\\''Neil'\\''s lesson/.mathimator/cache/media/it'\\''s.mp4'",
    ]
    manifest = tmp_path / "partial_movie_file_list.txt"
    manifest.write_text(listing, encoding="utf-8")
    assert read_manifest(manifest) == ["a.mp4", "it's.mp4"]


def test_the_segment_list_reader_takes_manims_own_lines(tmp_path: Path) -> None:
    manifest = tmp_path / "partial_movie_file_list.txt"
    manifest.write_text(
        "# This file records the segment order used by Manim.\n"
        "file 'file:C:/project/one.mp4'\n"
        "file 'file:C:\\project\\two.mp4'\n"
        "file \\'quoted\\'.mp4\n"
        "file ''\n",
        encoding="utf-8",
    )
    assert read_manifest(manifest) == ["one.mp4", "two.mp4", "'quoted'.mp4"]


def test_the_segment_list_matches_manims_for_plain_paths() -> None:
    writer = pytest.importorskip("manim.scene.scene_file_writer").SceneFileWriter
    original = inspect.getattr_static(writer, "_concat_manifest_bytes")
    # manim_main replaces this static method; a Manim upgrade that renames or reshapes it must fail here.
    assert isinstance(original, staticmethod)
    assert list(inspect.signature(original.__func__).parameters) == ["input_files"]
    files = ["C:/project/.mathimator/cache/media/a.mp4", "/home/user/project/b.webm"]
    assert concat_manifest_bytes(files) == original.__func__(files)


# Where Manim runs, and what it gets (D60, D61, D18)


def test_manim_runs_in_the_scene_folder_with_its_own_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    media = supervisor.layout.media_dir
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="render_preview", id="preview-1")
    codegen = str(supervisor.layout.scene_path.parent)
    seen = fake_manim.seen()
    assert seen["cwd"] == codegen
    assert seen["argv"][:3] == ["render", "scene.py", "MainScene"]
    assert seen["env"] == {
        "HOME": codegen,
        "USERPROFILE": codegen,
        "shell_escape": "f",
        "openin_any": "p",
        "openout_any": "p",
    }
    # manim_main replaced the segment list writer of the fake.
    assert seen["writer"] == "concat_manifest_bytes"
    assert f"cwd: {codegen}" in supervisor.log_path.read_text(encoding="utf-8")
    *_, done = stdout.of("preview-1")
    assert done["path"] == str(media / "videos" / "scene" / "480p15" / "MainScene.mp4")


def test_tex_inputs_planted_in_the_project_are_removed_before_manim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    tex = supervisor.layout.media_dir / "Tex"
    # TeX would load the package before its own, and Manim would compile the source or convert the DVI file.
    planted = ["amsmath.sty", f"{TEX_HASH}.tex", f"{TEX_HASH}.dvi", "nested/amsmath.sty"]
    for name in [*planted, f"{TEX_HASH}.svg"]:
        (tex / name).parent.mkdir(parents=True, exist_ok=True)
        (tex / name).write_text("planted", encoding="utf-8")
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="render_preview", id="preview-1")
    assert stdout.of("preview-1")[-1]["event"] == "done"
    # Manim reuses an SVG file, which draws and reads nothing.
    assert [name for name in fake_manim.seen()["files"] if name.startswith("Tex/")] == [f"Tex/{TEX_HASH}.svg"]
    assert [path.name for path in tex.iterdir()] == [f"{TEX_HASH}.svg"]


def test_a_link_in_the_tex_folder_goes_and_what_it_points_to_stays(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.tex").write_text("mine", encoding="utf-8")
    tex = tmp_path / "media" / "Tex"
    tex.mkdir(parents=True)
    _link_folder(tex / "linked", outside)
    _remove_tex_inputs(tex)
    assert list(tex.iterdir()) == []
    tex.rmdir()
    _link_folder(tex, outside)
    _remove_tex_inputs(tex)
    assert not os.path.lexists(tex)
    assert [path.name for path in outside.iterdir()] == ["notes.tex"]


def test_a_render_stops_when_a_planted_tex_input_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    tex = supervisor.layout.media_dir / "Tex"
    tex.mkdir(parents=True)
    (tex / "amsmath.sty").write_text("planted", encoding="utf-8")

    def in_use(path: Path) -> None:
        raise PermissionError(13, "The process cannot access the file", str(path))

    monkeypatch.setattr(supervisor_module, "remove_file", in_use)
    _run(supervisor, cmd="render_preview", id="preview-1")
    failed = stdout.of("preview-1")[-1]
    assert (failed["event"], failed["code"]) == ("error", "render_failed")
    assert failed["message"].startswith(f"could not remove the old TeX files in {tex}: ")
    assert not fake_manim.ran()
    assert (tex / "amsmath.sty").is_file()


def test_hand_written_mode_runs_in_the_scene_folder_with_no_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    monkeypatch.setenv("MATHIMATOR_TEX_ERROR", TEX_ERROR)
    supervisor = _supervisor(handwritten_project(tmp_path / "hand {written}", REFERENCE), monkeypatch)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")]))
    _run(supervisor, cmd="render_preview", id="preview-1")
    assert stdout.of("preview-1")[-1]["event"] == "done"
    assert fake_manim.seen()["cwd"] == str(supervisor.layout.scene_path.parent)


# Start checks (C7.2: codegen, TeX, path)


def test_a_scene_that_cannot_be_generated_is_refused_before_manim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    monkeypatch.setattr(supervisor_module, "generate", _refuse)
    _run(supervisor, cmd="render_preview", id="preview-1")
    assert stdout.of("preview-1") == [
        {"event": "rejected", "jobId": "preview-1", "code": "invalid_document", "message": CODEGEN_ERROR}
    ]
    assert not fake_manim.ran()
    assert "argv: " not in supervisor.log_path.read_text(encoding="utf-8")


def test_formulas_wait_for_tex_and_other_scenes_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    monkeypatch.setenv("MATHIMATOR_TEX_ERROR", TEX_ERROR)
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch)
    for command in ("render_preview", "export"):
        _run(supervisor, cmd=command, id=command)
        assert stdout.of(command) == [
            {"event": "rejected", "jobId": command, "code": "tex_unavailable", "message": TEX_ERROR}
        ]
    assert not fake_manim.ran()

    plain = _supervisor(_plain(tmp_path / "plain"), monkeypatch)
    fake_manim.plan(video_render(plain.layout, "480p15", [complete_clip(".mp4")] * 3))
    _run(plain, cmd="render_preview", id="preview-1")
    assert stdout.of("preview-1")[-1]["event"] == "done"


def test_a_scene_needs_latex_for_a_formula_or_labelled_axes(tmp_path: Path) -> None:
    journey = load_text(JOURNEY.read_text(encoding="utf-8"))
    assert needs_latex(journey)
    plain = load_text((_plain(tmp_path / "plain") / "project.json").read_text(encoding="utf-8"))
    assert not needs_latex(plain)
    plain.scene.objects[0].params["showLabels"] = True
    assert needs_latex(plain)


def test_a_path_manim_cannot_use_is_refused_before_manim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    folder = _journey(tmp_path / "draft {1}")
    supervisor = _supervisor(folder, monkeypatch)
    _run(supervisor, cmd="render_preview", id="preview-1")
    assert stdout.of("preview-1") == [
        {
            "event": "rejected",
            "jobId": "preview-1",
            "code": "path_unsupported",
            "message": f'Mathimator cannot render the project in {folder}: its path contains "{{". '
            "Move it to a shorter or plainer path.",
        }
    ]
    assert not fake_manim.ran()


def test_the_start_checks_run_in_the_contract_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    monkeypatch.setenv("MATHIMATOR_TEX_ERROR", TEX_ERROR)
    folder = _journey(tmp_path / "draft {1}")
    supervisor = _supervisor(folder, monkeypatch)
    # Every check comes before the export cache, which holds an export of this very document.
    assert supervisor.session is not None
    _cache_export(folder, supervisor.session.project)
    assert existing_export(supervisor.layout, supervisor.session.project) is not None
    saved = (folder / "project.json").read_bytes()
    with monkeypatch.context() as patched:
        patched.setattr(supervisor_module, "generate", _refuse)
        (folder / "project.json").write_bytes(saved + b"\n")
        _run(supervisor, cmd="export", id="fingerprint")
        (folder / "project.json").write_bytes(saved)
        _run(supervisor, cmd="export", id="codegen")
    _run(supervisor, cmd="export", id="tex")
    _run(supervisor, cmd="set_tex_error", id="clear-tex", message=None)
    _run(supervisor, cmd="export", id="path")
    codes = ["project_changed", "invalid_document", "tex_unavailable", "path_unsupported"]
    assert [(line["event"], line["code"]) for line in stdout.read()] == [("rejected", code) for code in codes]
    assert not fake_manim.ran()


# The path preflight (D61, G4.4, G4.6, G4.7)


def test_the_length_limits_are_those_of_tex_and_windows() -> None:
    # G4.7: dvisvgm fails from 211 characters. Python without long paths fails from 117 for a preview
    # clip, and from 115 for the longest, a 1080p60 WebM clip.
    assert longest_folder(TEX_FILE) == 210
    assert longest_folder(PARTIAL_FILE) == 114


def test_a_brace_is_refused_and_other_characters_pass() -> None:
    for name in ("Mr O'Neil's", "100% a~b", "x^^41", "brace}"):
        assert manim_root(LESSONS / name, latex=True) == LESSONS / name
    with pytest.raises(DocumentError) as refused:
        manim_root(LESSONS / "{draft}", latex=False)
    assert refused.value.code == "path_unsupported"


def test_a_path_outside_the_code_page_goes_through_its_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    project = LESSONS / "Ελληνικά"
    alias = LESSONS / "ELLHNI~1"
    ansi = {str(alias): True, str(project): False}
    monkeypatch.setattr(manim_paths, "_in_ansi_code_page", lambda text: ansi[text])
    monkeypatch.setattr(manim_paths, "short_path", lambda path: alias)
    assert manim_root(project, latex=True) == alias

    # No alias: only a scene without LaTeX renders, under the long name.
    monkeypatch.setattr(manim_paths, "short_path", lambda path: path)
    assert manim_root(project, latex=False) == project
    with pytest.raises(DocumentError, match="characters that Windows cannot pass to LaTeX") as refused:
        manim_root(project, latex=True)
    assert refused.value.code == "path_unsupported"
    assert str(project) in str(refused.value)


@pytest.mark.skipif(sys.platform != "win32", reason="MAX_PATH is a Windows limit")
def test_a_path_too_long_for_tex_or_windows_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def folder(length: int) -> Path:
        return Path("C:\\" + "x" * (length - 3))

    monkeypatch.setattr(manim_paths, "_long_paths_enabled", lambda: True)
    assert manim_root(folder(210), latex=True) == folder(210)
    with pytest.raises(DocumentError, match="labels need a folder path of at most 210 characters, not 211"):
        manim_root(folder(211), latex=True)
    # Long paths lift the limit for everything but TeX.
    assert manim_root(folder(211), latex=False) == folder(211)
    monkeypatch.setattr(manim_paths, "_long_paths_enabled", lambda: False)
    assert manim_root(folder(114), latex=False) == folder(114)
    with pytest.raises(DocumentError, match="without Windows long paths, renders need a folder path of at most 114"):
        manim_root(folder(115), latex=False)


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 aliases and the ANSI code page exist only on Windows")
def test_manim_runs_through_the_alias_and_events_keep_the_long_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    folder = _journey(tmp_path / "Ελληνικά μαθήματα")
    alias = short_path(folder)
    if alias == folder or not manim_paths._in_ansi_code_page(str(alias)):
        pytest.skip("the code page takes Greek, or this volume does not create 8.3 names")
    supervisor = _supervisor(folder, monkeypatch)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="render_preview", id="preview-1")
    assert fake_manim.seen()["cwd"] == str(alias / ".mathimator" / "codegen")
    # Every path sent is the bound folder's long path joined with a path inside it (C5).
    paths = [line["path"] for line in stdout.of("preview-1") if "path" in line]
    assert len(paths) == 5
    assert all(path.startswith(str(folder) + "\\") for path in paths)


# A project in a folder with an unusual name, through every command with the fake Manim (G4.10)


@pytest.mark.parametrize(
    "name",
    ["O'Neil's", "100% a~b", "x^^41 [v2]", "Ελληνικά", "📐 café"],
    ids=["apostrophe", "percent-tilde", "carets-brackets", "greek", "emoji"],
)
def test_a_folder_name_with_unusual_characters(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    """Every path the worker sends is the folder's own, character for character (C5).

    The scene has no formula, so a name outside the ANSI code page renders
    with or without an 8.3 alias. A "{" is refused before Manim starts.
    """
    folder = _plain(tmp_path / name)
    supervisor = _supervisor(folder, monkeypatch)
    root = str(folder.resolve())
    _run(supervisor, cmd="status", id="status")
    _run(supervisor, cmd="edit", id="rename", edit={"op": "set_project_name", "name": name})
    clips = [complete_clip(".mp4")] * 3
    fake_manim.plan(
        {**video_render(supervisor.layout, "480p15", clips), **video_render(supervisor.layout, "1080p30", clips)}
    )
    _run(supervisor, cmd="render_preview", id="preview-1")
    _run(supervisor, cmd="export", id="export-1")
    target = tmp_path / f"{name} copy"
    target.mkdir()
    _run(supervisor, cmd="save_as", id="save", path=str(target))
    _run(supervisor, cmd="open_project", id="open", path=str(folder))
    (status,) = stdout.of("status")
    (renamed,) = stdout.of("rename")
    preview, export = stdout.of("preview-1"), stdout.of("export-1")
    (saved,) = stdout.of("save")
    (opened,) = stdout.of("open")
    assert status["startupError"] is None
    assert (renamed["projectPath"], renamed["document"]["name"]) == (root, name)
    assert [line["event"] for line in preview] == ["started", "beat_ready", "beat_ready", "beat_ready", "done"]
    assert [line["event"] for line in export] == ["started", "done"]
    assert export[-1]["current"] is True
    for line in [*preview[1:], export[-1]]:
        assert line["path"].startswith(root + os.sep) and Path(line["path"]).is_file()
    copy = str(target.resolve())
    assert saved["projectPath"] == copy
    partials = [beat["partial"] for beat in saved["index"]["beats"]]
    assert all(path.startswith(copy + os.sep) and Path(path).is_file() for path in partials)
    assert (opened["projectPath"], opened["document"]["name"]) == (root, name)


# A formula rendered by Manim, LaTeX, dvisvgm and FFmpeg from such a folder (D61, G4.10)


@pytest.mark.render
@pytest.mark.parametrize("name", ["Mr O'Neil's 100% a~b", "Ελληνικά μαθήματα"], ids=["quotes", "greek"])
def test_a_formula_renders_from_a_folder_with_an_unusual_name(name: str) -> None:
    folder = RENDERS / name
    if folder.exists():
        shutil.rmtree(folder)
    _formula(folder)
    try:
        manim_root(folder, latex=True)
    except DocumentError as refused:
        pytest.skip(str(refused))
    client = start_supervisor(folder, folder.with_name(f"{folder.name}.log"))
    try:
        client.send({"cmd": "render_preview", "id": "preview-1"})
        finished = client.wait_for_end("preview-1", 600)
        partials = [Path(str(event["path"])) for event in client.events if event.get("event") == "beat_ready"]
    finally:
        client.close()
    assert finished["event"] == "done", finished
    movie = Path(str(finished["path"]))
    assert movie.is_relative_to(folder)
    assert movie.stat().st_size > 0
    # FFmpeg joined the partials through the segment list, which quotes an apostrophe.
    assert len(partials) == 2
    assert read_manifest(partials[0].parent / "partial_movie_file_list.txt") == [path.name for path in partials]


def _supervisor(project: Path, monkeypatch: pytest.MonkeyPatch) -> Supervisor:
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    return Supervisor(project, sys.executable, project.parent / f"{project.name}.log", False)


def _journey(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "project.json").write_bytes(JOURNEY.read_bytes())
    return root


def _plain(root: Path) -> Path:
    """Journey A without its formula and with unlabelled axes: a scene that needs no LaTeX."""
    document = json.loads(JOURNEY.read_text(encoding="utf-8"))
    scene = document["scene"]
    scene["objects"] = [item for item in scene["objects"] if item["type"] != "math_formula"]
    scene["objects"][0]["params"]["showLabels"] = False
    scene["beats"] = [beat for beat in scene["beats"] if beat["targets"] != ["title_1"]]
    root.mkdir(parents=True)
    (root / "project.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def _formula(root: Path) -> Path:
    """Journey A with only its formula: the shortest scene that runs LaTeX."""
    document = json.loads(JOURNEY.read_text(encoding="utf-8"))
    scene = document["scene"]
    scene["objects"] = [item for item in scene["objects"] if item["type"] == "math_formula"]
    scene["beats"] = [beat for beat in scene["beats"] if beat["targets"] in (["title_1"], [])]
    scene["templateInstances"] = []
    root.mkdir(parents=True)
    (root / "project.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def _cache_export(root: Path, project: Project) -> None:
    """A published export of ``project``, as the export cache finds it."""
    folder = root / ".mathimator" / "exports" / export_folder_name(project)
    folder.mkdir(parents=True)
    write_clip(folder / "scene.mp4")
    (folder / "export.json").write_text(json.dumps({"exportKey": export_key(project)}), encoding="utf-8")


def _link_folder(link: Path, target: Path) -> None:
    """A link to a folder that any account can make: a junction on Windows, a symbolic link elsewhere."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _refuse(project: object) -> str:
    raise CodegenError(CODEGEN_ERROR)


def _run(supervisor: Supervisor, **request: object) -> None:
    """Send a request and wait for the job it starts, if it starts one."""
    supervisor.handle_line(json.dumps(request))
    job: _Job | None = supervisor._current
    if job is not None:
        job.thread.join(timeout=60)
        assert not job.thread.is_alive()
