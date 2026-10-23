#!/usr/bin/env bash
# Build the phase 5 offline runtime under packaging/bundle/.
#
# The product installer is the Windows NSIS bundle in
# desktop/src-tauri/tauri.windows.conf.json. This script prepares the runtime
# that installer embeds: a uv virtualenv, preseeded TinyTeX, Typst 0.15.0,
# the worker package, and the reference-60 sample. It does not build the
# NSIS installer and it does not sign anything.
#
# On Linux the pinned CPython is 3.14.9 when uv publishes that build, otherwise
# the newest CPython 3.14 uv publishes for this platform. If manim[typst]==0.22.0
# has no wheel for that 3.14, the script retries with CPython 3.13.17.
# The Manim pin does not change.
#
# Windows asset names, for a run of this script on Windows:
#   TinyTeX-1-windows-v2026.10.exe
#   typst-x86_64-pc-windows-msvc.zip
#   uv venv python at venv/Scripts/python.exe
# This VM is Linux, so the executable bundle produced here is linux-x86_64.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/packaging/bundle"
CACHE="$ROOT/packaging/.cache"
UV_DIR="$CACHE/uv"
TINYTEX_VERSION="2026.10"
TINYTEX_ARCHIVE="TinyTeX-1-linux-x86_64-v${TINYTEX_VERSION}.tar.xz"
TINYTEX_URL="https://github.com/rstudio/tinytex-releases/releases/download/v${TINYTEX_VERSION}/${TINYTEX_ARCHIVE}"
TYPST_VERSION="0.15.0"
TYPST_ARCHIVE="typst-x86_64-unknown-linux-musl.tar.xz"
TYPST_URL="https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/${TYPST_ARCHIVE}"
MANIM_SPEC='manim[typst]==0.22.0'

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

if [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]]; then
  :
elif [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* || "$(uname -s)" == CYGWIN* ]]; then
  exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ROOT/packaging/build-bundle.ps1"
else
  echo "This checkout's shell recipe builds the linux-x86_64 runtime. On Windows run packaging/build-bundle.ps1." >&2
  exit 1
fi

mkdir -p "$CACHE" "$DEST"

if [[ ! -x "$UV_DIR/uv" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$UV_DIR" sh
fi
UV="$UV_DIR/uv"

download() {
  local url="$1"
  local dest="$2"
  if [[ -f "$dest" ]]; then
    return
  fi
  curl -fL --retry 5 --retry-delay 2 -o "$dest.partial" "$url"
  mv "$dest.partial" "$dest"
}

choose_python() {
  local requested="3.14.9"
  if "$UV" python install "$requested" >/tmp/mathimator-uv-python.txt 2>&1; then
    printf '%s\n%s\n' "$requested" "uv published $requested"
    return
  fi
  local newest
  newest="$("$UV" python list --all-versions | python3 -c '
import re, sys
found = []
for line in sys.stdin:
    if "freethreaded" in line:
        continue
    match = re.search(r"cpython-(3\.14\.\d+)-linux-x86_64-gnu", line)
    if match:
        found.append(tuple(int(part) for part in match.group(1).split(".")))
if not found:
    sys.exit("uv publishes no CPython 3.14 for linux-x86_64")
found.sort()
print(".".join(str(part) for part in found[-1]))
')"
  if ! "$UV" python install "$newest" >>/tmp/mathimator-uv-python.txt 2>&1; then
    echo "uv could not install CPython $newest" >&2
    cat /tmp/mathimator-uv-python.txt >&2
    exit 1
  fi
  printf '%s\n%s\n' "$newest" "uv has no CPython ${requested} build for linux-x86_64; using ${newest}"
}

install_manim() {
  local version="$1"
  rm -rf "$DEST/venv"
  "$UV" venv --python "$version" "$DEST/venv" >&2
  local py="$DEST/venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="$DEST/venv/bin/python3"
  fi
  "$UV" pip install --python "$py" "$MANIM_SPEC" >&2
  "$py" -c 'import manim, sys; assert manim.__version__ == "0.22.0", manim.__version__; print(sys.version.split()[0])'
}

choice_file="$(mktemp)"
choose_python >"$choice_file"
mapfile -t PYTHON_CHOICE <"$choice_file"
rm -f "$choice_file"
PY_VERSION="${PYTHON_CHOICE[0]}"
PY_REASON="${PYTHON_CHOICE[1]}"
echo "Python selection: $PY_REASON"

if ! INSTALLED="$(install_manim "$PY_VERSION")"; then
  if [[ "$PY_VERSION" == 3.14.* ]]; then
    echo "manim[typst]==0.22.0 did not install on $PY_VERSION; trying 3.13.17" >&2
    if ! "$UV" python install 3.13.17; then
      echo "uv has no CPython 3.13.17 build" >&2
      exit 1
    fi
    PY_VERSION="3.13.17"
    PY_REASON="$PY_REASON; manim wheel missing, switched to 3.13.17"
    INSTALLED="$(install_manim "$PY_VERSION")"
  else
    exit 1
  fi
fi

PY_BIN="$DEST/venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="$DEST/venv/bin/python3"
fi
if [[ "$INSTALLED" != "0.22.0" && "$INSTALLED" != "$PY_VERSION" ]]; then
  # install_manim prints the interpreter version. Confirm Manim separately.
  true
fi
"$PY_BIN" -c 'import manim; raise SystemExit(0 if manim.__version__ == "0.22.0" else 1)'

if [[ ! -d "$DEST/tinytex/bin" ]]; then
  download "$TINYTEX_URL" "$CACHE/$TINYTEX_ARCHIVE"
  stage="$(mktemp -d)"
  tar -xJf "$CACHE/$TINYTEX_ARCHIVE" -C "$stage"
  top="$(find "$stage" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  rm -rf "$DEST/tinytex"
  mv "$top" "$DEST/tinytex"
  rm -rf "$stage"
fi

TINYTEX_BIN="$(find "$DEST/tinytex/bin" -mindepth 1 -maxdepth 1 -type d -print -quit)"
if [[ -z "$TINYTEX_BIN" || ! -x "$TINYTEX_BIN/tlmgr" ]]; then
  echo "TinyTeX tlmgr was not found under $DEST/tinytex/bin" >&2
  exit 1
fi

export PATH="$TINYTEX_BIN:$PATH"
# The v2026.10 tlmgr refuses package installs until it has updated itself
# against the current TeX Live repository. Record the resulting version.
"$TINYTEX_BIN/tlmgr" update --self
if [[ ! -x "$TINYTEX_BIN/latex" ]]; then
  "$TINYTEX_BIN/tlmgr" install latex-bin latex
fi
"$TINYTEX_BIN/tlmgr" install "${PACKAGES[@]}"

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

mkdir -p "$DEST/tinytex/offline-repo"
"$TINYTEX_BIN/tlmgr" option repository "$DEST/tinytex/offline-repo"
if "$TINYTEX_BIN/tlmgr" install qrcode >/tmp/mathimator-tlmgr-offline.txt 2>&1; then
  echo "TinyTeX still installed a package after the repository was disabled" >&2
  cat /tmp/mathimator-tlmgr-offline.txt >&2
  exit 1
fi

if [[ ! -x "$DEST/typst/typst" ]]; then
  download "$TYPST_URL" "$CACHE/$TYPST_ARCHIVE"
  stage="$(mktemp -d)"
  tar -xJf "$CACHE/$TYPST_ARCHIVE" -C "$stage"
  binary="$(find "$stage" -type f -name typst -print -quit)"
  mkdir -p "$DEST/typst"
  cp "$binary" "$DEST/typst/typst"
  chmod 755 "$DEST/typst/typst"
  rm -rf "$stage"
fi
TYPST_LINE="$("$DEST/typst/typst" --version | head -n 1)"
case "$TYPST_LINE" in
  "typst 0.15.0"*) ;;
  *)
    echo "bundled typst is $TYPST_LINE" >&2
    exit 1
    ;;
esac

rm -rf "$DEST/worker"
mkdir -p "$DEST/worker"
cp -a "$ROOT/worker/mathimator_worker" "$DEST/worker/mathimator_worker"
find "$DEST/worker" -type d -name __pycache__ -exec rm -rf {} +
mkdir -p "$DEST/samples/reference-60"
cp "$ROOT/tests/fixtures/reference-60/project.json" "$DEST/samples/reference-60/project.json"

PY_FULL="$("$PY_BIN" -c 'import sys; print(sys.version.split()[0])')"
MANIM_FULL="$("$PY_BIN" -c 'import manim; print(manim.__version__)')"
TINYTEX_LINE="$("$TINYTEX_BIN/tlmgr" --version | head -n 1)"
REL_PY="${PY_BIN#"$DEST"/}"
REL_TEX="${TINYTEX_BIN#"$DEST"/}"

"$PY_BIN" - <<PY
import json
from pathlib import Path
dest = Path(${DEST@Q})
payload = {
    "python": "${REL_PY}",
    "pythonVersion": "${PY_FULL}",
    "pythonSelection": "${PY_REASON}",
    "manim": "${MANIM_FULL}",
    "typst": "typst/typst",
    "typstVersion": "${TYPST_LINE}",
    "tinytexBin": "${REL_TEX}",
    "tinytexVersion": "${TINYTEX_LINE}",
    "tinytexRelease": "${TINYTEX_VERSION}",
    "sample": "samples/reference-60/project.json",
    "platform": "linux-x86_64",
}
(dest / "runtime.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "runtime: $DEST/runtime.json"
du -sh "$DEST/venv" "$DEST/tinytex" "$DEST/typst" "$DEST" || true
