#!/usr/bin/env bash
# Install the Typst CLI that packaging/versions.env pins into DIR, for the
# test:typst job in ci/linux.yml:
#
#   bash ci/install-typst.sh DIR
#
# The archive is checked against TYPST_LINUX_SHA256 before it is unpacked, as
# the bundle scripts check theirs (D35), and the script fails unless DIR/typst
# then reports TYPST_VERSION. Needs curl, sha256sum, tar and xz.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${1:?usage: install-typst.sh DIR}"

# shellcheck source=../packaging/versions.env
. "$ROOT/packaging/versions.env"
if [[ -z "$TYPST_LINUX_SHA256" ]]; then
  echo "packaging/versions.env pins no TYPST_LINUX_SHA256" >&2
  exit 1
fi

ARCHIVE="$(mktemp)"
trap 'rm -f "$ARCHIVE"' EXIT
curl -fsSL --retry 5 --retry-delay 2 -o "$ARCHIVE" \
  "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz"
if ! printf '%s  %s\n' "$TYPST_LINUX_SHA256" "$ARCHIVE" | sha256sum --check --status; then
  echo "the Typst ${TYPST_VERSION} archive does not match TYPST_LINUX_SHA256" >&2
  exit 1
fi

mkdir -p "$DIR"
tar -xJf "$ARCHIVE" -C "$DIR" --strip-components=1 typst-x86_64-unknown-linux-musl/typst
LINE="$("$DIR/typst" --version | head -n 1)"
case "$LINE" in
  "typst $TYPST_VERSION" | "typst $TYPST_VERSION "*) echo "$LINE" ;;
  *)
    echo "$DIR/typst is $LINE, not typst $TYPST_VERSION" >&2
    exit 1
    ;;
esac
