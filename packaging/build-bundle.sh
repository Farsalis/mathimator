#!/usr/bin/env bash
# Build the offline runtime under packaging/bundle/.
#
# The product installer is the Windows NSIS bundle in
# desktop/src-tauri/tauri.windows.conf.json, so on Windows (Git Bash, MSYS2 or
# Cygwin) this script runs packaging/build-bundle.ps1. On Linux it builds the
# same runtime for linux-x86_64: a relocatable CPython with the Manim render
# stack from uv.lock installed into it, preseeded TinyTeX, Typst, the worker
# package, the reference-60 sample, and runtime.json. It does not build an
# installer and it does not sign anything.
#
# packaging/versions.env pins uv, CPython, TinyTeX, the TeX Live snapshot and
# Typst, with a SHA-256 for every download. The interpreter is PYTHON_VERSION,
# or PYTHON_FALLBACK_VERSION only when a locked package has no wheel for it.
# runtime.json is written last, after the bundle has passed its checks.
# scripts/generate_notices.py writes THIRD_PARTY_NOTICES.txt afterwards, and
# scripts/check_bundle.py checks the finished bundle. On Windows, `tauri
# build` runs both before it makes the installer (beforeBundleCommand).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/packaging/bundle"
CACHE="$ROOT/packaging/.cache"

if [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]]; then
  :
elif [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* || "$(uname -s)" == CYGWIN* ]]; then
  exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$ROOT/packaging/build-bundle.ps1")"
else
  echo "This checkout's shell recipe builds the linux-x86_64 runtime. On Windows run packaging/build-bundle.ps1." >&2
  exit 1
fi

# shellcheck source=versions.env
. "$ROOT/packaging/versions.env"
TINYTEX_ARCHIVE="TinyTeX-1-linux-x86_64-v${TINYTEX_VERSION}.tar.xz"
TINYTEX_URL="https://github.com/rstudio/tinytex-releases/releases/download/v${TINYTEX_VERSION}/${TINYTEX_ARCHIVE}"
TYPST_ARCHIVE="typst-${TYPST_VERSION}-x86_64-unknown-linux-musl.tar.xz"
TYPST_URL="https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz"
# Where tlmgr points once the bundle is built. The .invalid domain never
# resolves (RFC 6761), so tlmgr cannot download, and unlike an empty local
# folder it records no build path.
DISABLED_REPOSITORY="https://offline.invalid/tlnet"

# Packages imported by Manim 0.22's default MathTex template
# (\documentclass[preview]{standalone}, babel, amsmath, amssymb) plus dvisvgm.
# amssymb.sty is shipped by the amsfonts TeX Live package, not a package named amssymb.
PACKAGES=(
  standalone
  preview
  babel
  babel-english
  amsmath
  amsfonts
  dvisvgm
)

mkdir -p "$CACHE" "$DEST"
# Neither may survive from an older build: runtime.json is written last, and
# the notices describe the finished bundle.
rm -f "$DEST/runtime.json" "$DEST/THIRD_PARTY_NOTICES.txt"

download() {
  local url="$1"
  local dest="$2"
  local sha256="$3"
  if [[ -z "$sha256" ]]; then
    echo "packaging/versions.env pins no SHA-256 for $url" >&2
    exit 1
  fi
  if [[ -f "$dest" ]]; then
    if printf '%s  %s\n' "$sha256" "$dest" | sha256sum --check --status; then
      return
    fi
    echo "cached $dest does not match its pinned SHA-256; downloading it again" >&2
    rm -f "$dest"
  fi
  curl -fL --retry 5 --retry-delay 2 -o "$dest.partial" "$url"
  if ! printf '%s  %s\n' "$sha256" "$dest.partial" | sha256sum --check --status; then
    rm -f "$dest.partial"
    echo "SHA-256 of $url does not match the pinned $sha256" >&2
    exit 1
  fi
  mv "$dest.partial" "$dest"
}

# The uv release decides which CPython builds exist and resolves the lock, so a
# uv on PATH is never used. The cached binary is extracted again from the
# checked archive on every run.
UV_DIR="$CACHE/uv-$UV_VERSION"
download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" \
  "$CACHE/uv-$UV_VERSION-x86_64-unknown-linux-gnu.tar.gz" "$UV_LINUX_SHA256"
rm -rf "$UV_DIR"
mkdir -p "$UV_DIR"
tar -xzf "$CACHE/uv-$UV_VERSION-x86_64-unknown-linux-gnu.tar.gz" -C "$UV_DIR" --strip-components=1
UV="$UV_DIR/uv"
case "$("$UV" --version)" in
  "uv $UV_VERSION" | "uv $UV_VERSION "*) ;;
  *)
    echo "the uv archive does not hold uv $UV_VERSION" >&2
    exit 1
    ;;
esac

REQUIREMENTS="$CACHE/render-requirements.txt"
BUILD_CONSTRAINTS="$CACHE/render-build-constraints.txt"
PYTHON_CACHE="$CACHE/python"
PY_HOME="$DEST/python"
PY_BIN="$PY_HOME/bin/python3"

install_python() {
  # A real interpreter rather than a venv, whose bin/python would link to a
  # CPython that exists only on the build machine. --reinstall downloads the
  # build again on every run, so uv checks its SHA-256 instead of trusting the
  # tree a previous run extracted.
  local version="$1"
  local source="$PYTHON_CACHE/cpython-${version}-linux-x86_64-gnu"
  "$UV" python install "$version" --install-dir "$PYTHON_CACHE" --no-bin --reinstall
  if [[ ! -x "$source/bin/python3" ]]; then
    echo "uv did not install CPython $version at $source" >&2
    exit 1
  fi
  rm -rf "$PY_HOME"
  cp -a "$source" "$PY_HOME"
  # uv writes its install path over python-build-standalone's /install
  # placeholder in the sysconfig data. Put the placeholder back: the copy
  # lives elsewhere, and the runtime never builds extensions.
  find "$PY_HOME" -type f -exec grep -qIF "$source" {} \; -exec sed -i "s|$source|/install|g" {} +
}

sync_render_stack() {
  # Wheels only, except for packages that publish no wheel at all, so a
  # package without a wheel for this interpreter fails the resolution instead
  # of being compiled on the build machine. The copy keeps uv's
  # EXTERNALLY-MANAGED marker, which keeps pip out of the shipped interpreter.
  local args=(pip sync --python "$PY_BIN" --break-system-packages --require-hashes --only-binary :all:
    --build-constraints "$BUILD_CONSTRAINTS")
  local name
  for name in "${SOURCE_ONLY[@]}"; do
    args+=(--no-binary "$name")
  done
  SYNC_OUTPUT="$("$UV" "${args[@]}" "$REQUIREMENTS" 2>&1)"
  local status=$?
  printf '%s\n' "$SYNC_OUTPUT"
  return "$status"
}

# The render stack is the lock's render group, with the hashes uv.lock records.
"$UV" export --project "$ROOT" --frozen --only-group render --no-emit-workspace --quiet --output-file "$REQUIREMENTS"
install_python "$PYTHON_VERSION"
# A package with no wheel at all is built from its sdist, with the build
# backend pinned in the lock rather than the newest one on build day.
source_only="$("$PY_BIN" -I -B - "$ROOT/uv.lock" "$BUILD_CONSTRAINTS" <<'PY'
import sys, tomllib
from pathlib import Path
lock = tomllib.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
pins = lock['manifest'].get('build-constraints', [])
Path(sys.argv[2]).write_text(''.join(c['name'] + c['specifier'] + '\n' for c in pins), encoding='utf-8')
print(' '.join(p['name'] for p in lock['package'] if 'sdist' in p and not p.get('wheels')))
PY
)"
read -r -a SOURCE_ONLY <<<"$source_only"
PYTHON_SELECTION="CPython $PYTHON_VERSION"
if ! sync_render_stack; then
  # uv reports a package without a usable wheel this way. Any other failure,
  # such as the network, a hash or the disk, stops the build.
  missing="$(sed -n 's/.*Because \([^ ]*\) has no usable wheels.*/\1/p' <<<"$SYNC_OUTPUT" | head -n 1)"
  if [[ -z "$missing" ]]; then
    echo "uv pip sync failed on CPython $PYTHON_VERSION" >&2
    exit 1
  fi
  PYTHON_SELECTION="CPython $PYTHON_FALLBACK_VERSION: $missing has no CPython $PYTHON_VERSION wheel"
  echo "$PYTHON_SELECTION" >&2
  install_python "$PYTHON_FALLBACK_VERSION"
  if ! sync_render_stack; then
    echo "uv pip sync failed on CPython $PYTHON_FALLBACK_VERSION" >&2
    exit 1
  fi
fi
# Console-script launchers hold the build path of the interpreter. The worker
# runs Manim with python -m, so none is needed.
find "$PY_HOME/bin" -maxdepth 1 -type f -exec grep -qF "$PY_HOME" {} \; -delete
echo "bundled python: $PYTHON_SELECTION"

# The stamp names the TinyTeX release the tree was extracted from, so a version
# bump replaces the tree instead of reusing it.
TINYTEX_STAMP="$DEST/tinytex/.mathimator-tinytex-release"
if [[ ! -f "$TINYTEX_STAMP" || "$(<"$TINYTEX_STAMP")" != "$TINYTEX_VERSION" ]]; then
  download "$TINYTEX_URL" "$CACHE/$TINYTEX_ARCHIVE" "$TINYTEX_LINUX_SHA256"
  stage="$(mktemp -d)"
  tar -xJf "$CACHE/$TINYTEX_ARCHIVE" -C "$stage"
  top="$(find "$stage" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  rm -rf "$DEST/tinytex"
  mv "$top" "$DEST/tinytex"
  rm -rf "$stage"
  printf '%s\n' "$TINYTEX_VERSION" >"$TINYTEX_STAMP"
fi

TINYTEX_BIN="$(find "$DEST/tinytex/bin" -mindepth 1 -maxdepth 1 -type d -print -quit)"
if [[ -z "$TINYTEX_BIN" || ! -x "$TINYTEX_BIN/tlmgr" ]]; then
  echo "TinyTeX tlmgr was not found under $DEST/tinytex/bin" >&2
  exit 1
fi
export PATH="$TINYTEX_BIN:$PATH"

tlmgr() {
  # tlmgr writes its own path into the files it generates (language.dat), so
  # it runs from the TinyTeX root by a relative path.
  (cd "$DEST/tinytex" && "./${TINYTEX_BIN#"$DEST/tinytex/"}/tlmgr" "$@")
}

# A rerun finds the disabled repository from the last build, so point tlmgr at
# the pinned snapshot before anything reaches a repository. The v2026.10 tlmgr
# refuses package installs until it has updated itself.
tlmgr option repository "$TLNET_REPOSITORY"
tlmgr update --self
if [[ ! -x "$TINYTEX_BIN/latex" ]]; then
  tlmgr install latex-bin latex
fi
tlmgr install "${PACKAGES[@]}"

warmup="$(mktemp -d)"
cat >"$warmup/formula.tex" <<'EOF'
\documentclass[preview]{standalone}
\usepackage[english]{babel}
\usepackage{amsmath}
\usepackage{amssymb}
\begin{document}
\( y = \sin(x) \)
\end{document}
EOF
(
  cd "$warmup"
  "$TINYTEX_BIN/latex" -interaction=nonstopmode -halt-on-error formula.tex
  "$TINYTEX_BIN/dvisvgm" --page=1 --no-fonts --verbosity=0 --output=formula.svg formula.dvi
  test -s formula.svg
)
rm -rf "$warmup"

tlmgr option repository "$DISABLED_REPOSITORY"
if qrcode_output="$(tlmgr install qrcode 2>&1)"; then
  echo "TinyTeX still installed a package after the repository was disabled" >&2
  printf '%s\n' "$qrcode_output" >&2
  exit 1
fi
TINYTEX_LINE="$(tlmgr --version | head -n 1)"

# tlmgr leaves its temp files, package backups, logs and a cache of the remote
# package database behind. Several name the build path, and none is used
# offline.
rm -rf "$DEST/tinytex/temp"/* "$DEST/tinytex/tlpkg/backups"/* "$DEST/tinytex/tlpkg"/texlive.tlpdb.main.*
find "$DEST/tinytex/texmf-var/web2c" -type f -name '*.log' -delete

# Typst is Apache-2.0, so its LICENSE and NOTICE ship next to the binary. The
# directory is rebuilt from the checked archive on every run.
download "$TYPST_URL" "$CACHE/$TYPST_ARCHIVE" "$TYPST_LINUX_SHA256"
stage="$(mktemp -d)"
tar -xJf "$CACHE/$TYPST_ARCHIVE" -C "$stage"
binary="$(find "$stage" -type f -name typst -print -quit)"
rm -rf "$DEST/typst"
mkdir -p "$DEST/typst"
for name in typst LICENSE NOTICE; do
  if [[ ! -f "$(dirname "$binary")/$name" ]]; then
    echo "$name was not in $TYPST_ARCHIVE" >&2
    exit 1
  fi
  cp "$(dirname "$binary")/$name" "$DEST/typst/$name"
done
chmod 755 "$DEST/typst/typst"
rm -rf "$stage"
TYPST_LINE="$("$DEST/typst/typst" --version | head -n 1)"
case "$TYPST_LINE" in
  "typst $TYPST_VERSION" | "typst $TYPST_VERSION "*) ;;
  *)
    echo "bundled typst is $TYPST_LINE" >&2
    exit 1
    ;;
esac

rm -rf "$DEST/worker"
mkdir -p "$DEST/worker"
cp -a "$ROOT/worker/mathimator_worker" "$DEST/worker/mathimator_worker"
mkdir -p "$DEST/samples/reference-60"
cp "$ROOT/tests/fixtures/reference-60/project.json" "$DEST/samples/reference-60/project.json"

# The bundled interpreter must import Manim on its own: isolated mode reads no
# PYTHON* variable, user site or working directory, and nothing it loads may
# come from outside the bundle.
probe="$("$PY_BIN" -I -B - "$PY_HOME" <<'PY'
import sys
from pathlib import Path
import manim
home = Path(sys.argv[1]).resolve()
for path in (sys.prefix, sys.base_prefix, manim.__file__):
    if not Path(path).resolve().is_relative_to(home):
        sys.exit(f'{path} is outside {home}')
print(sys.version.split()[0], manim.__version__)
PY
)"
read -r PY_FULL MANIM_FULL <<<"$(tail -n 1 <<<"$probe")"
echo "bundled python $PY_FULL manim $MANIM_FULL"

# The Windows installer puts the runtime under Program Files, where
# Python run by a standard user cannot save the bytecode it compiles, so
# build-bundle.ps1 compiles the standard library, the render stack and the
# worker that every start of the supervisor and of Manim would compile again.
# This runtime gets the same bytecode, compiled in one process, which makes it
# the same on every build. Checked-hash bytecode holds a hash of its source,
# which Python checks on import, so a stale file never runs in place of its
# source. -s and -p record each source as runtime/... rather than by its build
# path, -f replaces any timestamped bytecode that the CPython build ships, and
# -W ignore keeps third-party SyntaxWarnings out of the log, as CPython's own
# install does.
"$PY_BIN" -I -W ignore -m compileall -q -f -s "$DEST" -p runtime --invalidation-mode checked-hash "$PY_HOME" "$DEST/worker"

# Everything in packaging/bundle ships. --prune removes entries outside the
# bundle layout, such as a crash dump or the venv of an older build, and any
# bytecode not compiled above, such as another Python version's, copied from
# the checkout with the worker. The check then fails if a link leaves the
# bundle or a file, bytecode included, names the checkout or the builder's
# home.
"$PY_BIN" -I -B "$ROOT/scripts/check_bundle.py" --prune "$DEST"

DEST="$DEST" \
  PY_FULL="$PY_FULL" \
  PYTHON_SELECTION="$PYTHON_SELECTION" \
  MANIM_FULL="$MANIM_FULL" \
  LOCK_SHA256="$(sha256sum "$ROOT/uv.lock" | cut -d ' ' -f 1)" \
  TYPST_LINE="$TYPST_LINE" \
  REL_TEX="${TINYTEX_BIN#"$DEST"/}" \
  TINYTEX_LINE="$TINYTEX_LINE" \
  TINYTEX_VERSION="$TINYTEX_VERSION" \
  TLNET_REPOSITORY="$TLNET_REPOSITORY" \
  "$PY_BIN" -I -B - <<'PY'
import json
import os
from pathlib import Path

env = os.environ
payload = {
    "python": "python/bin/python3",
    "worker": "worker",
    "pythonVersion": env["PY_FULL"],
    "pythonSelection": env["PYTHON_SELECTION"],
    "manim": env["MANIM_FULL"],
    "lockSha256": env["LOCK_SHA256"],
    "typst": "typst/typst",
    "typstVersion": env["TYPST_LINE"],
    "tinytexBin": env["REL_TEX"],
    "tinytexVersion": env["TINYTEX_LINE"],
    "tinytexRelease": env["TINYTEX_VERSION"],
    "tlnetRepository": env["TLNET_REPOSITORY"],
    "sample": "samples/reference-60/project.json",
    "platform": "linux-x86_64",
}
(Path(env["DEST"]) / "runtime.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "runtime: $DEST/runtime.json"
du -sh "$DEST"/* "$DEST" || true
