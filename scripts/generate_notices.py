"""Write THIRD_PARTY_NOTICES.txt for the installer from a built packaging/bundle.

    generate_notices.py [BUNDLE] [--output FILE]

BUNDLE defaults to packaging/bundle and FILE to BUNDLE/THIRD_PARTY_NOTICES.txt,
which the installer ships as runtime/THIRD_PARTY_NOTICES.txt (D75). On
Windows, `tauri build` runs it on the built bundle, then
scripts/check_bundle.py, which fails until it has run, before it makes the
installer (beforeBundleCommand in tauri.windows.conf.json). The bundle build
scripts delete the file, so a rebuilt bundle never ships the notices of an
older one.

The notices list, each with its version and declared licence:

- the bundled CPython, from the interpreter that runtime.json names, with
  each shared library it ships, such as OpenSSL and Tcl/Tk, and the
  libraries linked into it;
- every Python distribution in its site-packages, from the .dist-info
  metadata and licence files;
- the FFmpeg, x264, x265 and other libraries that PyAV vendors in av.libs,
  and those linked into FFmpeg such as LAME, with the FFmpeg version, licence
  and configure line read from avcodec, and a written offer of their source
  code;
- Typst, and TinyTeX with every TeX Live package in its package database;
- the Rust crates the shell links, from `cargo metadata` for the bundle's
  platform (normal and build dependencies, not dev ones);
- the npm packages the UI bundles: the non-dev entries of
  desktop/package-lock.json, read from desktop/node_modules;
- on Windows, the Microsoft Edge WebView2 Runtime, whose offline installer
  the installer embeds (webviewInstallMode in tauri.windows.conf.json).

Each distinct licence text is printed once at the end and cited as [Tn]. A
component that ships no licence file gets the standard text of its declared
licence from packaging/licenses, when that folder has one. A component left
with no text at all is named in a warning on stderr, and does not stop the
script. The header records the SHA-256 of uv.lock, Cargo.lock and
package-lock.json as "<path> SHA-256 <hex>" lines, which check_bundle.py
compares with the checkout. The file holds no absolute path and no date, so
the same inputs give the same bytes.

The script fails and writes nothing when runtime.json lacks a field it needs,
when `cargo metadata` fails (it runs offline, so build the shell or run
`cargo fetch` first), or when a runtime npm package is not installed (run
`npm ci` in desktop/). It also fails on a native library that no table
names: a shared library in the interpreter that CPYTHON_LIBRARIES does not
name, a platform whose linked libraries CPYTHON_LINKED does not list, a file
in av.libs that AV_LIBRARIES does not name, a library that FFmpeg's configure
line enables, ships no file of, and FFMPEG_LINKED does not name, or an FFmpeg
build whose licence FFMPEG_LICENCES does not know. The tables are kept by
hand, so a new native library is reviewed before an installer ships it.
"""

from __future__ import annotations

import argparse
import email.message
import email.parser
import hashlib
import json
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass, field
from operator import itemgetter
from pathlib import Path, PurePosixPath
from typing import Any

REPO = Path(__file__).resolve().parents[1]
NOTICES = "THIRD_PARTY_NOTICES.txt"
LOCKS = ("uv.lock", "desktop/src-tauri/Cargo.lock", "desktop/package-lock.json")
RUNTIME_FIELDS = ("platform", "python", "pythonVersion", "typst", "typstVersion", "tinytexBin", "tinytexRelease")
# Standard licence texts, verbatim as the FSF, Apache and Mozilla publish them,
# named by SPDX identifier without -only or -or-later.
STANDARD_TEXTS = REPO / "packaging" / "licenses"
# File and folder names that hold a package's licence material.
LICENCE_FILE = re.compile(r"(licen[cs]e|copying|copyright|notice|patents|authors|unlicense)", re.IGNORECASE)
# The Rust target the shell is built for on each runtime.json platform.
RUST_TARGETS = {"windows-x86_64": "x86_64-pc-windows-msvc", "linux-x86_64": "x86_64-unknown-linux-gnu"}
# CPython's own shared libraries, such as python314.dll and libpython3.14.so.
CPYTHON_OWN = re.compile(r"(lib)?python3\d*")
# The licence of the Microsoft Visual C++ runtime beside python.exe.
MSVC_RUNTIME = "Microsoft Distributable Code"
# The licences whose text the interpreter's LICENSE.txt carries after CPython's
# own: the Additional Conditions for the Microsoft runtime and, in the Windows
# build of python-build-standalone, the Tcl/Tk licence.
IN_CPYTHON_LICENCE = frozenset({MSVC_RUNTIME, "TCL"})
# The other shared libraries in the python-build-standalone interpreter, by
# file name up to the first - or .: the project, its licence and where its
# source is published.
CPYTHON_LIBRARIES = {
    "libcrypto": ("OpenSSL", "Apache-2.0", "https://www.openssl.org"),
    "libssl": ("OpenSSL", "Apache-2.0", "https://www.openssl.org"),
    "libffi": ("libffi", "MIT", "https://sourceware.org/libffi/"),
    "libtommath": ("LibTomMath", "Unlicense", "https://github.com/libtom/libtommath"),
    "sqlite3": ("SQLite", "blessing", "https://sqlite.org"),
    "tcl90": ("Tcl", "TCL", "https://www.tcl-lang.org"),
    "tcl9dde14": ("Tcl", "TCL", "https://www.tcl-lang.org"),
    "tcl9registry13": ("Tcl", "TCL", "https://www.tcl-lang.org"),
    "tcl9tk90": ("Tk", "TCL", "https://www.tcl-lang.org"),
    "vcruntime140": ("Microsoft Visual C++ runtime", MSVC_RUNTIME, "https://visualstudio.microsoft.com"),
    "vcruntime140_1": ("Microsoft Visual C++ runtime", MSVC_RUNTIME, "https://visualstudio.microsoft.com"),
    "zlib1": ("zlib", "Zlib", "https://zlib.net"),
}
# The libraries python-build-standalone links into CPython and its extension
# modules on each runtime.json platform, which ship no file of their own, with
# their licences.
CPYTHON_LINKED = {
    "windows-x86_64": {
        "bzip2": "bzip2-1.0.6",
        "Expat": "MIT",
        "HACL*": "MIT",
        "mimalloc": "MIT",
        "mpdecimal": "BSD-2-Clause",
        "xz": "0BSD",
        "zlib-ng": "Zlib",
        "Zstandard": "BSD-3-Clause",
    },
}
# FFmpeg's own libraries, under the licence that avcodec reports.
FFMPEG_LIBRARIES = frozenset({"avcodec", "avdevice", "avfilter", "avformat", "avutil", "swresample", "swscale"})
# Every licence that FFmpeg's avcodec_license() can report but "nonfree and
# unredistributable", as SPDX.
FFMPEG_LICENCES = {
    "GPL version 2 or later": "GPL-2.0-or-later",
    "GPL version 3 or later": "GPL-3.0-or-later",
    "LGPL version 2.1 or later": "LGPL-2.1-or-later",
    "LGPL version 3 or later": "LGPL-3.0-or-later",
}
# The other libraries in av.libs, by file name up to the first - or .: the
# project, its licence and where its source is published.
AV_LIBRARIES = {
    "libx264": ("x264", "GPL-2.0-or-later", "https://code.videolan.org/videolan/x264"),
    "libx265": ("x265", "GPL-2.0-or-later", "https://bitbucket.org/multicoreware/x265_git"),
    "libdav1d": ("dav1d", "BSD-2-Clause", "https://code.videolan.org/videolan/dav1d"),
    "libSvtAv1Enc": ("SVT-AV1", "BSD-3-Clause-Clear", "https://gitlab.com/AOMediaCodec/SVT-AV1"),
    "libopus": ("Opus", "BSD-3-Clause", "https://opus-codec.org"),
    "libvpx": ("libvpx", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libvpx"),
    "libwebp": ("libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"),
    "libwebpmux": ("libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"),
    "libsharpyuv": ("libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"),
    "libvmaf": ("VMAF", "BSD-2-Clause-Patent", "https://github.com/Netflix/vmaf"),
    "libvpl": ("Intel VPL", "MIT", "https://github.com/intel/libvpl"),
    "libiconv": ("GNU libiconv", "LGPL-2.1-or-later", "https://www.gnu.org/software/libiconv/"),
    "libgcc_s_seh": ("GCC runtime", "GPL-3.0-or-later WITH GCC-exception-3.1", "https://gcc.gnu.org"),
    "libstdc++": ("GCC C++ runtime", "GPL-3.0-or-later WITH GCC-exception-3.1", "https://gcc.gnu.org"),
    "libwinpthread": ("mingw-w64 winpthreads", "MIT AND BSD-3-Clause", "https://www.mingw-w64.org"),
    "zlib1": ("zlib", "Zlib", "https://zlib.net"),
}
# The libraries that FFmpeg's configure line enables with --enable-lib<name>
# and links into the FFmpeg libraries, with no file in av.libs: the project,
# its licence and where its source is published.
FFMPEG_LINKED = {"mp3lame": ("LAME", "LGPL-2.0-or-later", "https://lame.sourceforge.io")}
# D75: the GPL and LGPL libraries in av.libs ship with an offer of their source.
SOURCE_OFFER = """\
These libraries are used unmodified, as PyAV {av} ships them. The pyav-ffmpeg
project (https://github.com/PyAV-Org/pyav-ffmpeg) builds them for PyAV and
publishes its build scripts and the upstream sources it uses. The FFmpeg
{ffmpeg} source is at https://ffmpeg.org/releases/ffmpeg-{ffmpeg}.tar.xz, and
the source address of every other library is listed above.

Written offer: for at least three years after the Mathimator maintainers
distribute this copy of Mathimator, they will give any third party who asks
through the Mathimator issue tracker a complete machine-readable copy of the
corresponding source code of the libraries above that are licensed under the
GNU GPL or LGPL, for a charge no more than the cost of physically performing
the source distribution."""
# The Windows installer embeds Microsoft's WebView2 offline installer
# (webviewInstallMode in tauri.windows.conf.json), whose version is whatever
# Microsoft publishes when the installer is built.
WEBVIEW2 = (
    "The installer embeds Microsoft's offline installer of the Microsoft Edge WebView2 Runtime, and runs it when "
    "the PC lacks that runtime. Mathimator draws its window with WebView2. WebView2 is Microsoft software under "
    "Microsoft's own licence terms, distributed from https://developer.microsoft.com/microsoft-edge/webview2/, and "
    "is not covered by Mathimator's MIT licence."
)


class NoticeError(Exception):
    """Something the notices cannot be written without."""


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    licence: str
    texts: tuple[tuple[str, str], ...]
    """Each licence text, with the file name or SPDX identifier it came from."""

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"


@dataclass(frozen=True)
class FFmpeg:
    version: str
    licence: str
    configure: str

    @property
    def external(self) -> list[str]:
        """The libraries its configure line enables with --enable-lib<name>, by name."""
        return list(dict.fromkeys(re.findall(r"--enable-lib([\w-]+)", self.configure)))


@dataclass
class _Citation:
    number: int
    title: str
    text: str
    users: list[str] = field(default_factory=list)


class Texts:
    """The licence texts the notices cite, each printed once at the end as [Tn]."""

    def __init__(self) -> None:
        # By the text's words, so that copies that differ only in wrapping
        # or indentation are printed once.
        self._citations: dict[str, _Citation] = {}
        self.missing: list[str] = []
        """The components that have no licence text."""

    def cite(self, component: Component) -> str:
        if not component.texts:
            self.missing.append(component.label)
            return "[no licence text ships with it]"
        numbers: list[str] = []
        for title, text in component.texts:
            key = " ".join(text.split())
            citation = self._citations.setdefault(key, _Citation(len(self._citations) + 1, title, _normalize(text)))
            if component.label not in citation.users:
                citation.users.append(component.label)
            if f"T{citation.number}" not in numbers:
                numbers.append(f"T{citation.number}")
        return f"[{', '.join(numbers)}]"

    def render(self) -> list[str]:
        lines = ["Licence texts", "=============", ""]
        for citation in self._citations.values():
            lines.append(f"[T{citation.number}] {citation.title}")
            lines += textwrap.wrap(f"Used by: {', '.join(citation.users)}", 79, subsequent_indent="  ")
            lines += ["-" * 79, citation.text, "", ""]
        return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write THIRD_PARTY_NOTICES.txt from a built packaging/bundle.")
    parser.add_argument(
        "bundle",
        nargs="?",
        type=Path,
        default=REPO / "packaging" / "bundle",
        help="the bundle folder (default: %(default)s)",
    )
    parser.add_argument("--output", type=Path, help=f"where to write the notices (default: BUNDLE/{NOTICES})")
    args = parser.parse_args(argv)
    bundle: Path = args.bundle
    if not bundle.is_dir():
        parser.error(f"{bundle} is not a folder")
    try:
        runtime = read_runtime(bundle)
        target = RUST_TARGETS.get(runtime["platform"])
        if target is None:
            raise NoticeError(f"no Rust target is known for platform {runtime['platform']}")
        metadata = cargo_metadata(REPO / "desktop" / "src-tauri", target)
        notices, missing = generate(bundle, REPO, runtime, metadata)
    except NoticeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for label in missing:
        print(f"warning: no licence text for {label}", file=sys.stderr)
    output: Path = args.output or bundle / NOTICES
    output.write_text(notices, encoding="utf-8", newline="\n")
    print(f"wrote {output}")
    return 0


def generate(bundle: Path, repo: Path, runtime: dict[str, str], metadata: dict[str, Any]) -> tuple[str, list[str]]:
    """Return the notices and the components that have no licence text.

    runtime is bundle's runtime.json, repo the checkout it was built from, and
    metadata the shell's `cargo metadata`.
    """
    python_home = bundle / PurePosixPath(runtime["python"]).parts[0]
    distributions = sorted(python_home.glob("**/site-packages/*.dist-info"), key=lambda path: path.name.lower())
    texts = Texts()
    sections = [
        ("CPython", _cpython_section(python_home, runtime["pythonVersion"], runtime["platform"], texts)),
        ("Python packages", _rows([python_distribution(path) for path in distributions], texts)),
        ("FFmpeg and the libraries it loads (av.libs)", _av_section(distributions, texts)),
        ("Typst", _rows([typst(bundle / runtime["typst"], runtime["typstVersion"])], texts)),
        (
            "TinyTeX",
            _tinytex_section(bundle / PurePosixPath(runtime["tinytexBin"]).parts[0], runtime["tinytexRelease"], texts),
        ),
        ("Rust crates", _rows(rust_crates(metadata), texts)),
        ("npm packages", _rows(npm_packages(repo / "desktop"), texts)),
    ]
    if runtime["platform"].startswith("windows-"):
        sections.append(("Microsoft Edge WebView2 Runtime", textwrap.wrap(WEBVIEW2, 79, break_on_hyphens=False)))

    root = metadata["resolve"]["root"]
    version = next(package["version"] for package in metadata["packages"] if package["id"] == root)
    title = f"Third-party notices for Mathimator {version}"
    lines = [title, "=" * len(title), ""]
    lines += textwrap.wrap(
        "Mathimator is under the MIT licence. Its installer also ships the third-party software below. Each entry "
        "gives the version and declared licence, and cites its licence texts, which follow at the end as [T1], [T2] "
        "and so on.",
        79,
    )
    lines += ["", f"Generated by scripts/generate_notices.py for {runtime['platform']} from:"]
    lines += [f"  {lock} SHA-256 {_sha256(repo / lock)}" for lock in LOCKS]
    lines += ["", ""]
    for number, (heading, rows) in enumerate(sections, 1):
        heading = f"{number}. {heading}"
        lines += [heading, "-" * len(heading), "", *rows, "", ""]
    lines += texts.render()
    return "\n".join(lines).rstrip("\n") + "\n", texts.missing


def read_runtime(bundle: Path) -> dict[str, str]:
    """The runtime.json fields the notices need."""
    try:
        runtime = json.loads((bundle / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NoticeError(f"cannot read runtime.json: {error}") from error
    if not isinstance(runtime, dict):
        raise NoticeError("runtime.json is not a JSON object")
    fields: dict[str, str] = {}
    for name in RUNTIME_FIELDS:
        value = runtime.get(name)
        if not isinstance(value, str) or not value:
            raise NoticeError(f"runtime.json has no {name}")
        fields[name] = value
    return fields


def cpython(home: Path, version: str) -> Component:
    """The interpreter at home: LICENSE.txt is in the root on Windows and in lib/python3.x on Linux."""
    licence = next(
        (path for path in [home / "LICENSE.txt", *sorted(home.glob("lib/python3*/LICENSE.txt"))] if path.is_file()),
        None,
    )
    texts = () if licence is None else ((licence.name, _read_text(licence)),)
    return _component("CPython", version, "PSF-2.0", texts)


def cpython_libraries(home: Path, interpreter: Component) -> list[tuple[Component, str]]:
    """Each shared library in the interpreter at home but CPython's own, with its source address.

    Extension modules (.pyd, .cpython-*.so) and site-packages are left out:
    CPYTHON_LINKED names what the extension modules link in, and site-packages
    has its own section. A library under a licence in IN_CPYTHON_LICENCE
    cites the licence texts of interpreter, the CPython component.
    """
    files = sorted(
        (
            path
            for path in home.rglob("*")
            if (path.suffix.lower() == ".dll" or ".so" in path.suffixes)
            and ".cpython-" not in path.name
            and "site-packages" not in path.relative_to(home).parts
            and not CPYTHON_OWN.fullmatch(_stem(path))
            and path.is_file()
        ),
        key=lambda path: path.name.lower(),
    )
    unknown = [path.name for path in files if _stem(path) not in CPYTHON_LIBRARIES]
    if unknown:
        raise NoticeError(f"CPython ships libraries that CPYTHON_LIBRARIES does not name: {', '.join(unknown)}")
    libraries: list[tuple[Component, str]] = []
    for path in files:
        project, licence, source = CPYTHON_LIBRARIES[_stem(path)]
        texts = interpreter.texts if licence in IN_CPYTHON_LICENCE else ()
        libraries.append((_component(project, f"({path.name})", licence, texts), source))
    return libraries


def python_distribution(dist_info: Path) -> Component:
    metadata = _metadata(dist_info)
    return _component(
        metadata.get("Name", dist_info.name),
        metadata.get("Version", ""),
        _declared_licence(metadata),
        _licence_files(dist_info),
    )


def ffmpeg_build(avcodec: bytes) -> FFmpeg:
    """Read the version, licence and configure line that FFmpeg compiles into avcodec."""
    version = re.search(rb"FFmpeg version ([^\x00\s]+)", avcodec)
    licence = re.search(rb"libavcodec license: ([^\x00]+)\x00", avcodec)
    configure = next((line for line in re.findall(rb"\x00(--[^\x00]+)\x00", avcodec) if b"--enable-" in line), None)
    if version is None or licence is None or configure is None:
        raise NoticeError("avcodec in av.libs does not name its FFmpeg version, licence and configure line")
    reported = licence[1].decode("ascii", "replace")
    if reported not in FFMPEG_LICENCES:
        raise NoticeError(f"FFmpeg in av.libs reports the licence {reported!r}")
    return FFmpeg(
        version[1].decode("ascii", "replace"), FFMPEG_LICENCES[reported], configure.decode("ascii", "replace")
    )


def av_libraries(libs: Path) -> tuple[FFmpeg, list[tuple[Component, str]]]:
    """Return the FFmpeg build in libs, and each library it ships with its source address.

    Those are the files in libs, then each external library that FFmpeg
    enables and links in, which has no file whose name starts with lib<name>.
    """
    files = sorted((path for path in libs.iterdir() if path.is_file()), key=lambda path: path.name.lower())
    ffmpeg_files = [path for path in files if _stem(path).removeprefix("lib") in FFMPEG_LIBRARIES]
    avcodec = next((path for path in ffmpeg_files if _stem(path).removeprefix("lib") == "avcodec"), None)
    if avcodec is None:
        raise NoticeError(f"{libs.name} holds no avcodec library")
    ffmpeg = ffmpeg_build(avcodec.read_bytes())
    unknown = [path.name for path in files if path not in ffmpeg_files and _stem(path) not in AV_LIBRARIES]
    if unknown:
        raise NoticeError(f"{libs.name} holds libraries that AV_LIBRARIES does not name: {', '.join(unknown)}")
    linked = [
        name
        for name in ffmpeg.external
        if not any(path.name.lower().startswith(f"lib{name}".lower()) for path in files)
    ]
    unnamed = [name for name in linked if name not in FFMPEG_LINKED]
    if unnamed:
        raise NoticeError(
            f"FFmpeg in {libs.name} links libraries that FFMPEG_LINKED does not name: {', '.join(unnamed)}"
        )
    libraries: list[tuple[Component, str]] = []
    for path in files:
        project, licence, source = (
            (f"FFmpeg {ffmpeg.version}", ffmpeg.licence, "https://ffmpeg.org")
            if path in ffmpeg_files
            else AV_LIBRARIES[_stem(path)]
        )
        libraries.append((_component(project, f"({path.name})", licence, ()), source))
    for name in linked:
        project, licence, source = FFMPEG_LINKED[name]
        libraries.append((_component(project, "(linked into FFmpeg)", licence, ()), source))
    return ffmpeg, libraries


def typst(executable: Path, version_line: str) -> Component:
    """Typst, whose LICENSE and NOTICE ship next to the binary. version_line is `typst --version`."""
    return _component("Typst", version_line.removeprefix("typst "), "Apache-2.0", _licence_files(executable.parent))


def tex_packages(tinytex: Path) -> list[str]:
    """One line per package in TinyTeX's package database, with its TeX Catalogue licence."""
    rows: list[str] = []
    package: dict[str, str] = {}
    for line in [*(tinytex / "tlpkg" / "texlive.tlpdb").read_text(encoding="utf-8").splitlines(), ""]:
        if line and not line.startswith(" "):
            key, _, value = line.partition(" ")
            package.setdefault(key, value)
        elif not line and package:
            name = package.get("name", "")
            if not name.startswith("00texlive."):
                version = " ".join(filter(None, [package.get("catalogue-version"), f"(r{package.get('revision')})"]))
                rows.append(f"{name} {version}: {package.get('catalogue-license', 'not in the TeX Catalogue')}")
            package = {}
    return rows


def cargo_metadata(manifest_dir: Path, target: str) -> dict[str, Any]:
    """`cargo metadata` for target, run in manifest_dir so that its rust-toolchain.toml applies."""
    command = ["cargo", "metadata", "--format-version", "1", "--locked", "--offline", "--filter-platform", target]
    try:
        completed = subprocess.run(
            command, cwd=manifest_dir, capture_output=True, text=True, encoding="utf-8", check=False
        )
    except OSError as error:
        raise NoticeError(f"cannot run cargo: {error}") from error
    if completed.returncode != 0:
        raise NoticeError(f"cargo metadata failed: {completed.stderr.strip()}")
    metadata: dict[str, Any] = json.loads(completed.stdout)
    return metadata


def rust_crates(metadata: dict[str, Any]) -> list[Component]:
    """The crates the root package depends on, directly or not, except through dev-dependencies."""
    packages = {package["id"]: package for package in metadata["packages"]}
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    root = metadata["resolve"]["root"]
    linked: set[str] = set()
    pending = [root]
    while pending:
        for dependency in nodes[pending.pop()]["deps"]:
            shipped = any(kind["kind"] != "dev" for kind in dependency["dep_kinds"])
            if shipped and dependency["pkg"] not in linked:
                linked.add(dependency["pkg"])
                pending.append(dependency["pkg"])
    crates: list[Component] = []
    for package in sorted((packages[package_id] for package_id in linked), key=itemgetter("name", "version")):
        folder = Path(package["manifest_path"]).parent
        texts = _licence_files(folder)
        named = package.get("license_file")
        if named and named not in dict(texts) and (folder / named).is_file():
            texts += ((named, _read_text(folder / named)),)
        crates.append(
            _component(package["name"], package["version"], package["license"] or "see its licence file", texts)
        )
    return crates


def npm_packages(desktop: Path) -> list[Component]:
    """The packages that desktop/package-lock.json installs for runtime, not only for development."""
    lock = json.loads((desktop / "package-lock.json").read_text(encoding="utf-8"))
    packages: list[Component] = []
    for key, entry in lock["packages"].items():
        if not key or entry.get("dev") or entry.get("devOptional"):
            continue
        folder = desktop / key
        if not folder.is_dir():
            if entry.get("optional"):
                continue
            raise NoticeError(f"desktop/{key} is not installed: run npm ci in desktop/")
        name = key.rpartition("node_modules/")[2]
        packages.append(
            _component(name, entry["version"], entry.get("license", "not declared"), _licence_files(folder))
        )
    return sorted(packages, key=lambda package: package.name)


def _rows(components: Iterable[Component], texts: Texts) -> list[str]:
    return [f"  {component.label}: {component.licence} {texts.cite(component)}" for component in components]


def _sourced_rows(libraries: Iterable[tuple[Component, str]], texts: Texts) -> list[str]:
    """A row for each library, then its source address."""
    return [line for library, source in libraries for line in (*_rows([library], texts), f"    {source}")]


def _cpython_section(home: Path, version: str, platform: str, texts: Texts) -> list[str]:
    linked = CPYTHON_LINKED.get(platform)
    if linked is None:
        raise NoticeError(f"CPYTHON_LINKED does not list the libraries linked into CPython on {platform}")
    interpreter = cpython(home, version)
    minor = ".".join(version.split(".")[:2])
    return [
        *_rows([interpreter], texts),
        "",
        *textwrap.wrap(
            "It ships these shared libraries, each listed with its project, licence and source address:", 79
        ),
        "",
        *_sourced_rows(cpython_libraries(home, interpreter), texts),
        "",
        *textwrap.wrap(
            f"It links in {', '.join(f'{name} ({licence})' for name, licence in linked.items())}, which have no "
            "file of their own. CPython also incorporates code under other licences, which "
            f"https://docs.python.org/{minor}/license.html lists with their texts.",
            79,
            break_on_hyphens=False,
        ),
    ]


def _av_section(distributions: list[Path], texts: Texts) -> list[str]:
    av = next((path for path in distributions if _metadata(path).get("Name", "").lower() == "av"), None)
    if av is None:
        raise NoticeError("PyAV is not installed in the bundled interpreter")
    av_version = _metadata(av).get("Version", "")
    ffmpeg, libraries = av_libraries(av.parent / "av.libs")
    gpl = sorted(
        {library.name for library, _ in libraries if re.fullmatch(r"GPL-[\d.]+(-only|-or-later)", library.licence)}
    )
    return [
        *textwrap.wrap(
            f"PyAV {av_version} encodes and decodes video with FFmpeg {ffmpeg.version}, which it vendors in av.libs "
            f"with the libraries FFmpeg loads. The GNU GPL covers {', '.join(gpl) or 'none of them'}. Each file is "
            "listed with its project, licence and source address, followed by the libraries linked into FFmpeg:",
            79,
        ),
        "",
        *_sourced_rows(libraries, texts),
        "",
        *textwrap.wrap(
            f"FFmpeg reports the licence {ffmpeg.licence} and enables these external libraries: "
            f"{', '.join(ffmpeg.external)}. Its configure line:",
            79,
        ),
        *textwrap.wrap(ffmpeg.configure, 79, initial_indent="  ", subsequent_indent="  ", break_on_hyphens=False),
        "",
        *SOURCE_OFFER.format(av=av_version, ffmpeg=ffmpeg.version).splitlines(),
    ]


def _tinytex_section(root: Path, release: str, texts: Texts) -> list[str]:
    tinytex = _component("TinyTeX", release, "TeX Live, licences per package below", _licence_files(root))
    return [
        *_rows([tinytex], texts),
        "",
        "  TeX Live packages, with their TeX Catalogue licence:",
        *(f"    {row}" for row in tex_packages(root)),
    ]


def _component(name: str, version: str, licence: str, texts: tuple[tuple[str, str], ...]) -> Component:
    """A component, with the standard texts of its declared licence when it ships none."""
    return Component(name, version, licence, texts or _standard_texts(licence))


def _standard_texts(licence: str) -> tuple[tuple[str, str], ...]:
    """The packaging/licenses texts for the SPDX identifiers in licence, without their -only or -or-later."""
    # LGPL 2.0 or later may be used under LGPL 2.1, whose text stands in for it.
    licence = re.sub(r"\bLGPL-2\.0(-or-later|\+)", "LGPL-2.1", licence)
    identifiers = [
        re.sub(r"(-only|-or-later|\+)$", "", token)
        for token in re.findall(r"[\w.+-]+", licence)
        if token.upper() not in ("AND", "OR", "WITH")
    ]
    if "LGPL-3.0" in identifiers:
        # LGPL 3 is a set of additional permissions on top of GPL 3.
        identifiers.append("GPL-3.0")
    paths = [STANDARD_TEXTS / f"{identifier}.txt" for identifier in dict.fromkeys(identifiers)]
    return tuple((path.stem, _read_text(path)) for path in paths if path.is_file())


def _licence_files(folder: Path) -> tuple[tuple[str, str], ...]:
    """The licence files in folder, and every file in a licence folder such as a dist-info's licenses/."""
    files: list[Path] = []
    for entry in sorted(folder.iterdir()):
        if LICENCE_FILE.match(entry.name):
            files += [path for path in (sorted(entry.rglob("*")) if entry.is_dir() else [entry]) if path.is_file()]
    return tuple((path.relative_to(folder).as_posix(), _read_text(path)) for path in files)


def _metadata(dist_info: Path) -> email.message.Message:
    return email.parser.HeaderParser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8"))


def _declared_licence(metadata: email.message.Message) -> str:
    """License-Expression, else a one-line License, else the licence classifiers."""
    expression = metadata.get("License-Expression")
    if expression:
        return str(expression)
    licence = str(metadata.get("License", "")).strip()
    if licence and "\n" not in licence:
        return licence
    classifiers = [
        classifier.rpartition(" :: ")[2]
        for classifier in metadata.get_all("Classifier", [])
        if classifier.startswith("License :: ") and classifier != "License :: OSI Approved"
    ]
    return ", ".join(classifiers) or "not declared"


def _stem(path: Path) -> str:
    """A library's name without its version or hash, such as libx264 for libx264-165-0f71f37c.dll."""
    match = re.match(r"[\w+]+", path.name)
    return match[0] if match else path.name


def _read_text(path: Path) -> str:
    data = path.read_bytes().removeprefix(b"\xef\xbb\xbf")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
