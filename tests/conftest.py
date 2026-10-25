"""Shared test setup: no Job Objects by default, Typst tests skipped without Typst, the fake Manim, and --update-golden.

Windows enables the job unless ``MATHIMATOR_WIN32_JOBS=0``. The reap tests in
``test_win32_job.py`` stay skipped unless ``MATHIMATOR_WIN32_TESTS=1``, and
only that opt-in is allowed to turn the job back on.

Tests marked ``typst`` run the ``typst`` binary from PATH. They are skipped,
not failed, when PATH has no ``typst``. Tests marked ``process_table`` are
skipped off Windows where there is no ``/proc`` to walk, as on macOS, which is
untested (D47).

The ``fake_manim`` fixture puts ``support.FakeManim`` on PYTHONPATH for the
tests that run jobs without Manim, ``stdout`` reads what an in-process
supervisor wrote, and ``link_kind`` runs a test once for each kind of folder
link. ``--update-golden`` makes ``test_shell_fixture.py`` rewrite the golden
``tests/fixtures/protocol/replies.json`` instead of comparing with it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.support import LINK_KINDS, FakeManim, Stdout


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="rewrite tests/fixtures/protocol/replies.json from the worker's replies in test_shell_fixture.py",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    skips: dict[str, pytest.MarkDecorator] = {}
    if shutil.which("typst") is None:
        skips["typst"] = pytest.mark.skip(reason="typst is not on PATH")
    if sys.platform != "win32" and not Path("/proc").is_dir():
        skips["process_table"] = pytest.mark.skip(reason="there is no /proc to walk")
    for item in items:
        for marker, skip in skips.items():
            if item.get_closest_marker(marker) is not None:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def keep_win32_jobs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("MATHIMATOR_WIN32_TESTS") == "1":
        return
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")


@pytest.fixture
def fake_manim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeManim:
    return FakeManim(tmp_path / "fake-manim", monkeypatch)


@pytest.fixture
def stdout(capsys: pytest.CaptureFixture[str]) -> Stdout:
    return Stdout(capsys)


@pytest.fixture(params=LINK_KINDS)
def link_kind(request: pytest.FixtureRequest) -> str:
    """Each kind of folder link this platform has, for ``link_folder``."""
    kind: str = request.param
    return kind
