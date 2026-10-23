"""Job Object cancel and quit. Not executed until Craig approves it.

Set ``MATHIMATOR_WIN32_TESTS=1`` to run these. They also set
``MATHIMATOR_WIN32_JOBS=1`` so the supervisor assigns each Manim child to a
kill-on-close job. Do not set either variable on this machine until then.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MATHIMATOR_WIN32_TESTS") != "1",
    reason=(
        "Win32 job-object tests are written but not executed until Craig "
        "approves running them on this machine"
    ),
)


def test_cancel_reaps_manim_latex_and_dvisvgm_within_one_second(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "1")
    pytest.fail("this body must not run unless MATHIMATOR_WIN32_TESTS=1")


def test_quit_reaps_manim_latex_and_dvisvgm_within_one_second(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "1")
    pytest.fail("this body must not run unless MATHIMATOR_WIN32_TESTS=1")
