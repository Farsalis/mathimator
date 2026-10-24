"""scripts/generate_notices.py: THIRD_PARTY_NOTICES.txt from a built bundle (D75)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import check_bundle
import generate_notices
import pytest
from generate_notices import Component, NoticeError, Texts

AVCODEC = (
    b"MZ\x00FFmpeg version 9.0.2\x00libavcodec license: LGPL version 3 or later\x00"
    b"--disable-static --prefix=/c/vendor --enable-libx264 --enable-libmp3lame --enable-libx264\x00"
)
RUNTIME = {
    "platform": "windows-x86_64",
    "python": "python/python.exe",
    "pythonVersion": "3.14.9",
    "typst": "typst/typst.exe",
    "typstVersion": "typst 0.15.0 (3ae52774)",
    "tinytexBin": "tinytex/bin/windows",
    "tinytexRelease": "2026.10",
}
TLPDB = """\
name 00texlive.config
depend minrelease/2016

name amsmath
revision 79234
shortdesc AMS mathematical facilities for LaTeX
runfiles size=200
 texmf-dist/tex/latex/amsmath/amsmath.sty
catalogue-license lppl1.3c
catalogue-version 2.17

name dvisvgm.windows
revision 77973
"""


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def dist_info(site: Path, name: str, version: str, headers: str, files: dict[str, str]) -> None:
    folder = site / f"{name}-{version}.dist-info"
    write(folder / "METADATA", f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{headers}\nThe README.\n")
    write(folder / "RECORD", "")
    for file, text in files.items():
        write(folder / file, text)


def make_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    write(bundle / "runtime.json", json.dumps(RUNTIME))
    write(bundle / "python" / "python.exe", "binary")
    write(bundle / "python" / "LICENSE.txt", "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2\n")
    write(bundle / "python" / "python314.dll", b"MZ")
    write(bundle / "python" / "vcruntime140.dll", b"MZ")
    write(bundle / "python" / "DLLs" / "libcrypto-3-x64.dll", b"MZ")
    write(bundle / "python" / "DLLs" / "tcl90.dll", b"MZ")
    write(bundle / "python" / "DLLs" / "_ssl.pyd", b"MZ")
    site = bundle / "python" / "Lib" / "site-packages"
    write(site / "numpy.libs" / "libscipy_openblas64_-13e2df51.dll", b"MZ")
    dist_info(site, "av", "19.0.2", "License-Expression: BSD-3-Clause", {"licenses/LICENSE.txt": "Copyright PyAV\n"})
    dist_info(site, "pycairo", "1.29.2", "License: LGPL-2.1-only OR MPL-1.1", {})
    dist_info(
        site,
        "scipy",
        "1.18.1",
        "License: Copyright (c) 2001-2002 Enthought, Inc.\n        All rights reserved.\n"
        "Classifier: License :: OSI Approved\nClassifier: License :: OSI Approved :: BSD License",
        {"LICENSE.txt": "Copyright SciPy Developers\n"},
    )
    libs = site / "av.libs"
    write(libs / "avcodec-63-2756d4ff.dll", AVCODEC)
    write(libs / "avutil-61-e540ec79.dll", b"MZ")
    write(libs / "libx264-165-0f71f37c.dll", b"MZ")
    write(libs / "zlib1-4d767210.dll", b"MZ")
    write(bundle / "typst" / "typst.exe", "binary")
    write(bundle / "typst" / "LICENSE", "Typst Apache License\n")
    write(bundle / "typst" / "NOTICE", "Typst notice\n")
    write(bundle / "tinytex" / "bin" / "windows" / "latex.exe", "binary")
    write(bundle / "tinytex" / "LICENSE.TL", "COPYING CONDITIONS FOR TeX Live\n")
    write(bundle / "tinytex" / "LICENSE.CTAN", "CTAN conditions\n")
    write(bundle / "tinytex" / "tlpkg" / "texlive.tlpdb", TLPDB)
    return bundle


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    write(repo / "uv.lock", "version = 1\n")
    write(repo / "desktop" / "src-tauri" / "Cargo.lock", "version = 4\n")
    lock = {
        "name": "mathimator",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "mathimator", "version": "0.1.0"},
            "node_modules/react": {"version": "19.3.1", "license": "MIT"},
            "node_modules/vite": {"version": "8.0.0", "license": "MIT", "dev": True},
            "node_modules/fsevents": {"version": "2.3.3", "license": "MIT", "optional": True},
        },
    }
    write(repo / "desktop" / "package-lock.json", json.dumps(lock))
    write(repo / "desktop" / "node_modules" / "react" / "LICENSE", "Copyright Meta\n")
    write(repo / "desktop" / "node_modules" / "react" / "index.js", "")
    return repo


def make_metadata(tmp_path: Path) -> dict[str, Any]:
    """`cargo metadata` for a shell with a normal, a build and a dev dependency."""
    crates = tmp_path / "registry"

    def package(
        name: str, licence: str | None, files: dict[str, str], license_file: str | None = None
    ) -> dict[str, Any]:
        for file, text in files.items():
            write(crates / name / file, text)
        write(crates / name / "Cargo.toml", "")
        return {
            "id": name,
            "name": name,
            "version": "1.0.0",
            "license": licence,
            "license_file": license_file,
            "manifest_path": str(crates / name / "Cargo.toml"),
        }

    def node(name: str, *deps: tuple[str, str | None]) -> dict[str, Any]:
        return {"id": name, "deps": [{"pkg": pkg, "dep_kinds": [{"kind": kind, "target": None}]} for pkg, kind in deps]}

    return {
        "packages": [
            package("mathimator", "MIT", {}),
            package("serde", "MIT OR Apache-2.0", {"LICENSE-MIT": "Copyright serde\n"}),
            package("cc", "MIT OR Apache-2.0", {"LICENSE-MIT": "Copyright   serde\n"}),
            package("proptest", "MIT OR Apache-2.0", {"LICENSE-MIT": "Copyright proptest\n"}),
            package("tauri-plugin", "Apache-2.0 OR MIT", {}),
            package("webview2-com", "MIT", {}),
            package("ring", None, {"README.md": "", "terms.txt": "ring terms\n"}, license_file="terms.txt"),
        ],
        "resolve": {
            "root": "mathimator",
            "nodes": [
                node("mathimator", ("serde", None), ("cc", "build"), ("proptest", "dev"), ("ring", None)),
                node("serde", ("tauri-plugin", None)),
                node("cc", ("webview2-com", None)),
                node("proptest", ("serde", None)),
                node("tauri-plugin"),
                node("webview2-com"),
                node("ring"),
            ],
        },
    }


def error_from(call: Callable[[], object]) -> str:
    try:
        call()
    except NoticeError as error:
        return str(error)
    raise AssertionError("expected a NoticeError")


def test_the_notices_cover_every_part_of_the_installer(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    repo = make_repo(tmp_path)

    notices, missing = generate_notices.generate(bundle, repo, RUNTIME, make_metadata(tmp_path))

    lines = notices.splitlines()
    assert lines[0] == "Third-party notices for Mathimator 1.0.0"
    for row in (
        "  CPython 3.14.9: PSF-2.0 [T1]",
        "  OpenSSL (libcrypto-3-x64.dll): Apache-2.0 [T2]",
        "    https://www.openssl.org",
        "  Tcl (tcl90.dll): TCL [T1]",
        "  Microsoft Visual C++ runtime (vcruntime140.dll): Microsoft Distributable Code [T1]",
        "  av 19.0.2: BSD-3-Clause [T3]",
        "  pycairo 1.29.2: LGPL-2.1-only OR MPL-1.1 [T4]",
        "  scipy 1.18.1: BSD License [T5]",
        "  FFmpeg 9.0.2 (avcodec-63-2756d4ff.dll): LGPL-3.0-or-later [T6, T7]",
        "  FFmpeg 9.0.2 (avutil-61-e540ec79.dll): LGPL-3.0-or-later [T6, T7]",
        "  x264 (libx264-165-0f71f37c.dll): GPL-2.0-or-later [T8]",
        "    https://code.videolan.org/videolan/x264",
        "  zlib (zlib1-4d767210.dll): Zlib [no licence text ships with it]",
        "  LAME (linked into FFmpeg): LGPL-2.0-or-later [T4]",
        "    https://lame.sourceforge.io",
        "  Typst 0.15.0 (3ae52774): Apache-2.0 [T9, T10]",
        "  TinyTeX 2026.10: TeX Live, licences per package below [T11, T12]",
        "    amsmath 2.17 (r79234): lppl1.3c",
        "    dvisvgm.windows (r77973): not in the TeX Catalogue",
        "  cc 1.0.0: MIT OR Apache-2.0 [T13]",
        "  ring 1.0.0: see its licence file [T14]",
        "  serde 1.0.0: MIT OR Apache-2.0 [T13]",
        "  tauri-plugin 1.0.0: Apache-2.0 OR MIT [T2]",
        "  webview2-com 1.0.0: MIT [no licence text ships with it]",
        "  react 19.3.1: MIT [T15]",
    ):
        assert row in lines, row
    names = ("00texlive", "proptest", "vite", "fsevents", "mathimator 0.1.0", "python314.dll", "_ssl", "openblas")
    assert not any(name in notices for name in names)
    prose = " ".join(notices.split())
    assert "It links in bzip2 (bzip2-1.0.6), Expat (MIT), HACL* (MIT)," in prose
    assert "Zstandard (BSD-3-Clause), which have no file of their own." in prose
    assert "https://docs.python.org/3.14/license.html" in prose
    assert "The GNU GPL covers x264." in prose
    assert "libraries: x264, mp3lame." in prose
    assert "https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz" in prose
    assert "Written offer:" in prose
    assert "8. Microsoft Edge WebView2 Runtime" in lines
    assert "embeds Microsoft's offline installer of the Microsoft Edge WebView2 Runtime" in prose
    assert " https://developer.microsoft.com/microsoft-edge/webview2/, " in prose
    assert missing == ["zlib (zlib1-4d767210.dll)", "webview2-com 1.0.0"]

    texts = (generate_notices.STANDARD_TEXTS / "LGPL-2.1.txt").read_text(encoding="utf-8")
    assert "[T1] LICENSE.txt Used by: CPython 3.14.9, Tcl (tcl90.dll), Microsoft Visual C++ runtime" in prose
    assert "[T4] LGPL-2.1\nUsed by: pycairo 1.29.2, LAME (linked into FFmpeg)\n" in notices
    assert texts.rstrip("\n") in notices
    assert "[T8] GPL-2.0\nUsed by: x264 (libx264-165-0f71f37c.dll)\n" in notices
    assert "[T13] LICENSE-MIT\nUsed by: serde 1.0.0, cc 1.0.0\n" not in notices
    assert "[T13] LICENSE-MIT\nUsed by: cc 1.0.0, serde 1.0.0\n" in notices
    assert notices.endswith("\n") and not notices.endswith("\n\n")
    assert generate_notices.generate(bundle, repo, RUNTIME, make_metadata(tmp_path))[0] == notices


def test_only_windows_notices_name_the_embedded_webview2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the Windows installer embeds the WebView2 offline installer (tauri.windows.conf.json)."""
    monkeypatch.setitem(generate_notices.CPYTHON_LINKED, "linux-x86_64", {})
    linux = {**RUNTIME, "platform": "linux-x86_64"}

    notices, _ = generate_notices.generate(make_bundle(tmp_path), make_repo(tmp_path), linux, make_metadata(tmp_path))

    assert "7. npm packages" in notices.splitlines()
    assert "WebView2 Runtime" not in notices


def test_check_bundle_accepts_the_notices_until_a_lockfile_changes(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    repo = make_repo(tmp_path)
    notices, _ = generate_notices.generate(bundle, repo, RUNTIME, make_metadata(tmp_path))
    write(bundle / check_bundle.NOTICES, notices)

    assert generate_notices.LOCKS == check_bundle.LOCKS
    for lock in check_bundle.LOCKS:
        assert f"  {lock} SHA-256 {hashlib.sha256((repo / lock).read_bytes()).hexdigest()}\n" in notices
    assert check_bundle._notices_problems(bundle, repo) == []
    write(repo / "desktop" / "package-lock.json", "{}")
    assert check_bundle._notices_problems(bundle, repo) == [
        "THIRD_PARTY_NOTICES.txt predates desktop/package-lock.json: run scripts/generate_notices.py"
    ]


def test_texts_that_differ_only_in_whitespace_are_printed_once() -> None:
    texts = Texts()
    first = Component("serde", "1.0.0", "MIT", (("LICENSE-MIT", "Copyright serde\n\nPermission granted.\n"),))
    second = Component("cc", "1.0.0", "MIT", (("LICENSE", "  Copyright serde\r\n\r\nPermission   granted."),))

    assert texts.cite(first) == "[T1]"
    assert texts.cite(second) == "[T1]"
    assert texts.render()[3:6] == ["[T1] LICENSE-MIT", "Used by: serde 1.0.0, cc 1.0.0", "-" * 79]


def test_av_libs_must_hold_only_reviewed_libraries(tmp_path: Path) -> None:
    libs = make_bundle(tmp_path) / "python" / "Lib" / "site-packages" / "av.libs"

    write(libs / "libfdk-aac-2-1a2b3c4d.dll", b"MZ")
    assert error_from(lambda: generate_notices.av_libraries(libs)) == (
        "av.libs holds libraries that AV_LIBRARIES does not name: libfdk-aac-2-1a2b3c4d.dll"
    )
    (libs / "avcodec-63-2756d4ff.dll").unlink()
    assert error_from(lambda: generate_notices.av_libraries(libs)) == "av.libs holds no avcodec library"


def test_ffmpeg_must_link_only_reviewed_libraries(tmp_path: Path) -> None:
    libs = make_bundle(tmp_path) / "python" / "Lib" / "site-packages" / "av.libs"

    configure = b"--enable-libx264 --enable-libsvtav1 --enable-libfdk-aac\x00"
    write(libs / "avcodec-63-2756d4ff.dll", AVCODEC.replace(b"--enable-libx264\x00", configure))
    assert error_from(lambda: generate_notices.av_libraries(libs)) == (
        "FFmpeg in av.libs links libraries that FFMPEG_LINKED does not name: svtav1, fdk-aac"
    )
    write(libs / "libSvtAv1Enc-7a2b2046.dll", b"MZ")
    assert error_from(lambda: generate_notices.av_libraries(libs)) == (
        "FFmpeg in av.libs links libraries that FFMPEG_LINKED does not name: fdk-aac"
    )


def test_cpython_must_ship_only_reviewed_libraries(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    repo = make_repo(tmp_path)
    home = bundle / "python"
    interpreter = generate_notices.cpython(home, "3.14.9")

    assert [(library.label, source) for library, source in generate_notices.cpython_libraries(home, interpreter)] == [
        ("OpenSSL (libcrypto-3-x64.dll)", "https://www.openssl.org"),
        ("Tcl (tcl90.dll)", "https://www.tcl-lang.org"),
        ("Microsoft Visual C++ runtime (vcruntime140.dll)", "https://visualstudio.microsoft.com"),
    ]
    write(home / "DLLs" / "libunknown-1.dll", b"MZ")
    assert error_from(lambda: generate_notices.cpython_libraries(home, interpreter)) == (
        "CPython ships libraries that CPYTHON_LIBRARIES does not name: libunknown-1.dll"
    )
    linux = {**RUNTIME, "platform": "linux-x86_64"}
    assert error_from(lambda: generate_notices.generate(bundle, repo, linux, make_metadata(tmp_path))) == (
        "CPYTHON_LINKED does not list the libraries linked into CPython on linux-x86_64"
    )


def test_ffmpeg_must_report_a_known_licence() -> None:
    build = generate_notices.ffmpeg_build(AVCODEC)
    assert (build.version, build.licence) == ("9.0.2", "LGPL-3.0-or-later")
    assert build.configure.startswith("--disable-static --prefix=/c/vendor")

    nonfree = AVCODEC.replace(b"LGPL version 3 or later", b"nonfree and unredistributable")
    assert error_from(lambda: generate_notices.ffmpeg_build(nonfree)) == (
        "FFmpeg in av.libs reports the licence 'nonfree and unredistributable'"
    )
    assert "does not name" in error_from(lambda: generate_notices.ffmpeg_build(b"MZ"))


def test_a_runtime_npm_package_must_be_installed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    for path in sorted((repo / "desktop" / "node_modules" / "react").iterdir()):
        path.unlink()
    (repo / "desktop" / "node_modules" / "react").rmdir()

    assert error_from(lambda: generate_notices.npm_packages(repo / "desktop")) == (
        "desktop/node_modules/react is not installed: run npm ci in desktop/"
    )


def test_runtime_json_must_name_what_the_notices_read(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    write(bundle / "runtime.json", json.dumps({**RUNTIME, "typst": ""}))

    assert error_from(lambda: generate_notices.read_runtime(bundle)) == "runtime.json has no typst"
    assert generate_notices.read_runtime(make_bundle(tmp_path / "other")) == RUNTIME
