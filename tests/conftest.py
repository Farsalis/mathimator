"""Shared test setup: no Job Objects by default, Typst tests skipped without Typst, and the fake Manim.

Windows enables the job unless ``MATHIMATOR_WIN32_JOBS=0``. The reap tests in
``test_win32_job.py`` stay skipped unless ``MATHIMATOR_WIN32_TESTS=1``, and
only that opt-in is allowed to turn the job back on.

Tests marked ``typst`` run the ``typst`` binary from PATH. They are skipped,
not failed, when PATH has no ``typst``. Tests marked ``process_table`` are
skipped off Windows where there is no ``/proc`` to walk, as on macOS, which is
untested (D47).

The ``fake_manim`` fixture puts ``support.FakeManim`` on PYTHONPATH for the
tests that run jobs in-process without Manim, and ``stdout`` reads what an
in-process supervisor wrote.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.support import FakeManim, Stdout


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
