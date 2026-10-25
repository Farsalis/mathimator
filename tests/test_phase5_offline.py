"""Offline proof for the phase 5 bundle. Requires packaging/build-bundle.sh.

Manim runs with the worker's own command line and environment, in a project's
codegen folder (D60, D61), with the worker from this checkout. runtime.json
must name the bundled worker, this checkout's uv.lock and the Python the build
chose, and Typst's LICENSE and NOTICE must ship beside it.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from mathimator_worker.argv import build_manim_argv, manim_environment
from tests.support import REPO, WORKER, sha256_file

pytestmark = pytest.mark.offline

BUNDLE = REPO / "packaging" / "bundle"
SCENE = textwrap.dedent(
    """\
    from manim import *


    class MainScene(Scene):
        def construct(self):
            self.camera.background_color = "#000000"
            title = MathTex(r"y = \\sin(x)")
            self.play(Write(title), run_time=0.4)
            self.wait(0.2)
    """
)


def test_bundled_typst_version_is_0_15_0() -> None:
    manifest = _manifest()
    typst = BUNDLE / str(manifest["typst"])
    output = subprocess.run(
        [str(typst), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    line = output.stdout.strip().splitlines()[0]
    assert line.startswith("typst 0.15.0")
    assert not line.startswith("typst 0.14")
    assert not line.startswith("typst 0.16")


def test_typst_ships_with_its_license_and_notice() -> None:
    """Typst is Apache-2.0, so its LICENSE and NOTICE ship beside the binary."""
    typst = BUNDLE / str(_manifest()["typst"])
    for name in ("LICENSE", "NOTICE"):
        assert (typst.parent / name).is_file(), f"{name} is not beside {typst}"


def test_runtime_json_names_the_worker_the_lock_and_the_python_chosen() -> None:
    manifest = _manifest()
    assert (BUNDLE / str(manifest["worker"]) / "mathimator_worker" / "__main__.py").is_file()
    assert manifest["lockSha256"] == sha256_file(REPO / "uv.lock")
    # "CPython 3.14.9", or the fallback and why: "CPython 3.13.17: av has no CPython 3.14.9 wheel".
    chosen = str(manifest["pythonSelection"]).split(":", 1)[0]
    assert chosen == f"CPython {manifest['pythonVersion']}"


@pytest.mark.skipif(
    os.name == "nt",
    reason="unshare --net is Linux-only; Windows uses the PATH-isolated bundle test",
)
def test_bundled_runtime_renders_mathtex_with_network_blocked(tmp_path: Path) -> None:
    manifest = _manifest()
    python = BUNDLE / str(manifest["python"])
    tinytex = BUNDLE / str(manifest["tinytexBin"])
    latex = _tool(tinytex, "latex")
    dvisvgm = _tool(tinytex, "dvisvgm")
    codegen = _scene_folder(tmp_path)
    argv = build_manim_argv(str(python), "preview")
    script = tmp_path / "offline_check.py"
    script.write_text(
        textwrap.dedent(
            f"""\
            import os
            import socket
            import subprocess
            import sys

            try:
                socket.create_connection(("1.1.1.1", 443), 3)
            except OSError as exc:
                print(f"NETWORK_BLOCKED {{exc}}")
            else:
                print("NETWORK_OPEN", file=sys.stderr)
                sys.exit(2)

            latex = {str(latex)!r}
            dvisvgm = {str(dvisvgm)!r}
            which = subprocess.run(["/bin/sh", "-c", "command -v latex && command -v dvisvgm"], check=True, capture_output=True, text=True)
            print(which.stdout)
            if which.stdout.splitlines()[0] != latex:
                sys.exit("latex is not the bundled binary")
            if which.stdout.splitlines()[1] != dvisvgm:
                sys.exit("dvisvgm is not the bundled binary")
            subprocess.run([latex, "--version"], check=True)
            subprocess.run([dvisvgm, "--version"], check=True)
            imported = subprocess.run([sys.executable, "-c", "import manim; print(manim.__version__)"], check=True, capture_output=True, text=True)
            print(imported.stdout.strip())
            if imported.stdout.strip() != "0.22.0":
                sys.exit("manim pin drifted")
            subprocess.run({argv!r}, cwd={str(codegen)!r}, check=True)
            """
        ),
        encoding="utf-8",
    )
    env = _bundle_environment(codegen, [str(tinytex), "/usr/bin", "/bin"])
    result = subprocess.run(
        ["unshare", "--net", "--map-root-user", "--", str(python), str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert "NETWORK_BLOCKED" in result.stdout
    assert "0.22.0" in result.stdout
    movies = list((codegen.parent / "cache" / "media").rglob("MainScene.mp4"))
    assert movies, result.stdout
    assert movies[0].stat().st_size > 0


@pytest.mark.skipif(os.name != "nt", reason="Windows bundle layout")
def test_windows_bundled_runtime_renders_mathtex_without_user_tex(tmp_path: Path) -> None:
    """Render with the bundled interpreter and TinyTeX, and with user TeX off PATH.

    This account cannot disconnect the NIC. A closed proxy and the empty
    tlmgr repository make a package download fail if the seed is incomplete.
    """
    manifest = _manifest()
    python = BUNDLE / str(manifest["python"])
    tinytex = BUNDLE / str(manifest["tinytexBin"])
    latex = _tool(tinytex, "latex")
    dvisvgm = _tool(tinytex, "dvisvgm")
    codegen = _scene_folder(tmp_path)
    argv = build_manim_argv(str(python), "preview")
    script = tmp_path / "offline_check.py"
    script.write_text(
        textwrap.dedent(
            f"""\
            import os
            import shutil
            import subprocess
            import sys

            latex = {str(latex)!r}
            dvisvgm = {str(dvisvgm)!r}
            found_latex = shutil.which("latex")
            found_dvisvgm = shutil.which("dvisvgm")
            print(found_latex)
            print(found_dvisvgm)
            if os.path.normcase(found_latex or "") != os.path.normcase(latex):
                sys.exit("latex is not the bundled binary")
            if os.path.normcase(found_dvisvgm or "") != os.path.normcase(dvisvgm):
                sys.exit("dvisvgm is not the bundled binary")
            subprocess.run([latex, "--version"], check=True)
            subprocess.run([dvisvgm, "--version"], check=True)
            imported = subprocess.run(
                [sys.executable, "-c", "import manim; print(manim.__version__)"],
                check=True,
                capture_output=True,
                text=True,
            )
            print(imported.stdout.strip())
            if imported.stdout.strip() != "0.22.0":
                sys.exit("manim pin drifted")
            subprocess.run({argv!r}, cwd={str(codegen)!r}, check=True)
            """
        ),
        encoding="utf-8",
        newline="\n",
    )
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    env = _bundle_environment(
        codegen, [str(tinytex), os.path.join(system_root, "System32"), os.path.join(system_root, "SysWOW64")]
    )
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["ALL_PROXY"] = "http://127.0.0.1:9"
    env["http_proxy"] = "http://127.0.0.1:9"
    env["https_proxy"] = "http://127.0.0.1:9"
    env["all_proxy"] = "http://127.0.0.1:9"
    result = subprocess.run(
        [str(python), str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert "0.22.0" in result.stdout
    movies = list((codegen.parent / "cache" / "media").rglob("MainScene.mp4"))
    assert movies, result.stdout
    assert movies[0].stat().st_size > 0


def _bundle_environment(codegen: Path, path: list[str]) -> dict[str, str]:
    """The worker's Manim environment for ``codegen``, with ``path`` as PATH and no user TeX or Python settings.

    Python writes no bytecode, in the bundle or anywhere else: bytecode there
    would name this checkout, and scripts/check_bundle.py would refuse the
    bundle. Unlike ``-B``, the variable reaches the Python that runs Manim.
    """
    env = manim_environment(os.environ, codegen)
    for key in (
        "TEXINPUTS",
        "TEXMFHOME",
        "TEXMFLOCAL",
        "TEXMFCNF",
        "TEXMFVAR",
        "TEXMFCONFIG",
        "TLMGR_REPOSITORY",
        "PYTHONPATH",
        "PYTHONHOME",
    ):
        env.pop(key, None)
    env["PATH"] = os.pathsep.join(path)
    env["PYTHONPATH"] = str(WORKER)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _scene_folder(root: Path) -> Path:
    """A project's codegen folder holding the test scene, where Manim runs."""
    codegen = root / "project" / ".mathimator" / "codegen"
    codegen.mkdir(parents=True)
    (codegen / "scene.py").write_text(SCENE, encoding="utf-8", newline="\n")
    return codegen


def _manifest() -> dict[str, object]:
    path = BUNDLE / "runtime.json"
    if not path.is_file():
        pytest.fail("packaging/bundle is missing. Run packaging/build-bundle.sh before this test.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        pytest.fail("runtime.json is not an object")
    return payload


def _tool(directory: Path, name: str) -> Path:
    for candidate in (directory / name, directory / f"{name}.exe"):
        if candidate.is_file():
            return candidate
    pytest.fail(f"{name} was not in {directory}")
