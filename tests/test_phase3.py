"""Phase 3 equation preview.

The Typst glyph is not a scene render. The integral test is the one Manim
render: an unsupported formula still becomes ``MathTex``. The 200 ms equation
budget is not judged here. ``compile_seconds`` is the Typst process only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mathimator_worker.codegen import generate
from mathimator_worker.document import dumps_project, empty_project, load_text
from mathimator_worker.equation import translate
from mathimator_worker.session import Session
from tests.support import REPO, media_dir, probe_video, scene_path, start_supervisor, venv_python

SINE = r"y = \sin(x)"
SINE_DOCUMENT = "#set page(width: auto, height: auto, margin: 8pt, fill: none)\n$ y = sin(x) $\n"
INTEGRAL = r"\int_0^1 x dx"
PHASE3 = REPO / "out" / "phase3"

# The golden translator cases, shared with the shell's translator tests. A null
# "typst" is a formula with no live preview.
_CASES = json.loads((REPO / "tests" / "fixtures" / "equation" / "translate-cases.json").read_text(encoding="utf-8"))
_SUPPORTED = [(case["latex"], case["typst"]) for case in _CASES if case["typst"] is not None]
_UNSUPPORTED = [case["latex"] for case in _CASES if case["typst"] is None]

# Everyday formulas whose letter runs Typst would read as one unknown name.
_SPLIT_LETTERS = [
    "E = mc^2",
    "y = mx + b",
    "ax^2+bx+c",
    r"\frac{dy}{dx}",
    r"\sin\theta",
    r"x\sin(x)",
    r"x\frac{a}{b}",
    "x2",
    "x_ab",
    r"\pi2",
]


@pytest.mark.parametrize(("latex", "math"), _SUPPORTED)
def test_supported_formula_matches_the_golden_file(latex: str, math: str) -> None:
    result = translate(latex)
    assert result.supported is True
    assert result.math == math
    assert result.document == f"#set page(width: auto, height: auto, margin: 8pt, fill: none)\n$ {math} $\n"


@pytest.mark.parametrize("latex", _UNSUPPORTED)
def test_unsupported_formula_has_no_document(latex: str) -> None:
    result = translate(latex)
    assert result.supported is False
    assert result.math is None
    assert result.document is None


def test_golden_file_has_the_split_letter_formulas() -> None:
    assert set(_SPLIT_LETTERS) <= {latex for latex, _ in _SUPPORTED}


@pytest.mark.typst
@pytest.mark.parametrize("latex", _SPLIT_LETTERS)
def test_split_letter_formula_compiles_to_an_svg(latex: str, tmp_path: Path) -> None:
    result = translate(latex)
    assert result.document is not None
    svg = tmp_path / "formula.svg"
    _compile(result.document, tmp_path / "formula.typ", svg)
    assert "<svg" in svg.read_text(encoding="utf-8")


def test_sine_document_matches_the_spec() -> None:
    result = translate(SINE)
    assert result.document == SINE_DOCUMENT


@pytest.mark.typst
def test_typst_version_is_0_15_0() -> None:
    completed = subprocess.run(
        ["typst", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.startswith("typst 0.15.0")


@pytest.mark.typst
def test_sine_svg_compiles(tmp_path: Path) -> None:
    result = translate(SINE)
    assert result.document == SINE_DOCUMENT
    directory = tmp_path / "sine"
    source = directory / "formula.typ"
    svg = directory / "formula.svg"
    directory.mkdir()
    source.write_text(result.document or "", encoding="utf-8")
    started = time.perf_counter()
    completed = subprocess.run(
        ["typst", "compile", str(source), str(svg)],
        check=False,
        capture_output=True,
    )
    elapsed = time.perf_counter() - started
    assert completed.returncode == 0, completed.stderr.decode()
    assert svg.is_file()
    body = svg.read_text(encoding="utf-8")
    assert svg.stat().st_size > 0
    assert "<svg" in body
    assert "<path" in body or "<use" in body
    version = subprocess.run(
        ["typst", "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _write(
        PHASE3 / "typst-compile.json",
        {
            "typst_version": version,
            "latex": SINE,
            "compile_seconds": elapsed,
            "svg_bytes": svg.stat().st_size,
            "debounce_ms": 100,
            "note": "compile_seconds is the typst process only. The 100 ms debounce is the inspector timer. The 200 ms budget was not judged.",
        },
    )


def test_integral_does_not_write_an_svg(tmp_path: Path) -> None:
    result = translate(INTEGRAL)
    assert result.supported is False
    assert result.document is None
    destination = tmp_path / "integral.svg"
    assert not destination.exists()


def test_cli_returns_the_sine_document() -> None:
    completed = subprocess.run(
        [str(venv_python()), "-m", "mathimator_worker.equation"],
        input=json.dumps({"latex": SINE}).encode(),
        capture_output=True,
        check=False,
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "worker"), "PYTHONUNBUFFERED": "1"},
    )
    assert completed.returncode == 0, completed.stderr.decode()
    payload = json.loads(completed.stdout)
    assert payload == {"supported": True, "document": SINE_DOCUMENT}


def test_cli_refuses_an_integral() -> None:
    completed = subprocess.run(
        [str(venv_python()), "-m", "mathimator_worker.equation"],
        input=json.dumps({"latex": INTEGRAL}).encode(),
        capture_output=True,
        check=False,
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "worker"), "PYTHONUNBUFFERED": "1"},
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert json.loads(completed.stdout) == {"supported": False}


def test_translator_is_outside_the_supervisor() -> None:
    package = REPO / "worker" / "mathimator_worker"
    for path in package.glob("*.py"):
        if path.name == "equation.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "mathimator_worker.equation" not in text
        # Clear cache deletes the spec's `.mathimator/typst` directory. That
        # path is not the translator, which stays in equation.py.
        without_cache_dir = text.replace('"typst"', "")
        assert "typst" not in without_cache_dir.lower()


def test_journey_a_codegen_is_still_mathtex() -> None:
    spec = (REPO / "docs" / "design-spec.md").read_text(encoding="utf-8")
    expected = _fenced(spec, "### Journey A, normative output", "python")
    project = load_text((REPO / "tests" / "fixtures" / "journey-a" / "project.json").read_text(encoding="utf-8"))
    source = generate(project)
    assert source == expected
    assert "MathTex" in source
    assert "TypstMath" not in source
    assert "Typst(" not in source


@pytest.mark.typst
def test_failed_typst_run_leaves_the_previous_svg(tmp_path: Path) -> None:
    good = translate(SINE)
    assert good.document is not None
    directory = tmp_path / "glyph"
    directory.mkdir()
    previous = directory / "formula-1.svg"
    _compile(good.document, directory / "formula-1.typ", previous)
    broken = translate(r"\sqrt{}")
    assert broken.supported is True
    assert broken.document is not None
    assert "sqrt()" in broken.document
    failed = directory / "formula-2.svg"
    completed = subprocess.run(
        ["typst", "compile", str(_write_typ(directory / "formula-2.typ", broken.document)), str(failed)],
        check=False,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert previous.is_file()
    assert previous.stat().st_size > 0


@pytest.mark.render
def test_unsupported_integral_still_renders_through_mathtex() -> None:
    project = PHASE3 / "integral"
    if project.exists():
        shutil.rmtree(project)
    project.mkdir(parents=True)
    session = Session(empty_project("Integral"))
    added = session.edit({"op": "add_formula", "latex": INTEGRAL})
    assert added.focus_beat_id is not None
    session.edit({"op": "set_beat_duration", "beatId": added.focus_beat_id, "duration": 0.5})
    source = generate(session.project)
    assert "MathTex" in source
    assert "TypstMath" not in source
    assert "Typst(" not in source
    assert INTEGRAL in source
    (project / "project.json").write_text(dumps_project(session.project), encoding="utf-8")
    log_path = PHASE3 / "logs" / "integral.log"
    client = start_supervisor(project, log_path)
    try:
        client.send({"cmd": "status", "id": "integral-status"})
        client.wait_for(
            lambda event: event.get("event") == "ready" and event.get("jobId") == "integral-status",
            30,
        )
        client.send({"cmd": "render_preview", "id": "integral-preview"})
        finished = client.wait_for(
            lambda event: event.get("jobId") == "integral-preview" and event.get("event") in {"done", "error"},
            180,
        )
    finally:
        client.close()
    assert finished.get("event") == "done", json.dumps(finished, indent=2)
    preview = Path(str(finished["path"]))
    duration, width, height = probe_video(preview)
    assert (width, height) == (854, 480)
    assert duration > 0
    written = scene_path(project).read_text(encoding="utf-8")
    assert "MathTex" in written
    assert "TypstMath" not in written
    assert "Typst(" not in written
    assert preview.is_file()
    assert media_dir(project).is_dir()
    _write(
        PHASE3 / "integral-preview.json",
        {
            "path": str(preview),
            "duration": duration,
            "width": width,
            "height": height,
            "latex": INTEGRAL,
        },
    )


def _compile(document: str, source: Path, svg: Path) -> None:
    source.write_text(document, encoding="utf-8")
    completed = subprocess.run(
        ["typst", "compile", "--ignore-system-fonts", str(source), str(svg)],
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr.decode()


def _write_typ(path: Path, document: str) -> Path:
    path.write_text(document, encoding="utf-8")
    return path


def _fenced(spec: str, heading: str, language: str) -> str:
    start = spec.index(heading)
    fence = f"```{language}\n"
    begin = spec.index(fence, start) + len(fence)
    end = spec.index("```", begin)
    return spec[begin:end]


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
