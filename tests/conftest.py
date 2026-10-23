"""Keep this machine's suite from creating Job Objects.

Windows enables the job unless ``MATHIMATOR_WIN32_JOBS=0``. The reap tests in
``test_win32_job.py`` stay skipped unless ``MATHIMATOR_WIN32_TESTS=1``, and
only that opt-in is allowed to turn the job back on.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def keep_win32_jobs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("MATHIMATOR_WIN32_TESTS") == "1":
        return
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")
