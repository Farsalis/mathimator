"""Shared test setup: no Job Objects by default, Typst tests skipped without Typst, and the fake Manim.

Windows enables the job unless ``MATHIMATOR_WIN32_JOBS=0``. The reap tests in
``test_win32_job.py`` stay skipped unless ``MATHIMATOR_WIN32_TESTS=1``, and
only that opt-in is allowed to turn the job back on.

Tests marked ``typst`` run the ``typst`` binary from PATH. They are skipped,
not failed, when PATH has no ``typst``.

The ``fake_manim`` fixture puts ``support.FakeManim`` on PYTHONPATH for the
tests that run jobs in-process without Manim.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.support import FakeManim


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if shutil.which("typst") is not None:
        return
    missing = pytest.mark.skip(reason="typst is not on PATH")
    for item in items:
        if item.get_closest_marker("typst") is not None:
            item.add_marker(missing)


@pytest.fixture(autouse=True)
def keep_win32_jobs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("MATHIMATOR_WIN32_TESTS") == "1":
        return
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")


@pytest.fixture
def fake_manim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeManim:
    return FakeManim(tmp_path / "fake-manim", monkeypatch)
