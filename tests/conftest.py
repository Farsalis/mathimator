"""Keep this machine's suite from creating Job Objects, and skip Typst tests without Typst.

Windows enables the job unless ``MATHIMATOR_WIN32_JOBS=0``. The reap tests in
``test_win32_job.py`` stay skipped unless ``MATHIMATOR_WIN32_TESTS=1``, and
only that opt-in is allowed to turn the job back on.

Tests marked ``typst`` run the ``typst`` binary from PATH. They are skipped,
not failed, when PATH has no ``typst``.
"""

from __future__ import annotations

import os
import shutil

import pytest


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
