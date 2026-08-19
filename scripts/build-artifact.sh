#!/usr/bin/env bash
# Build the release artifact for this platform and print its digest.
#
# The two things a marketplace entry needs are the binary and its
# sha256, so this prints the digest rather than leaving the publisher to
# remember a second command — a published hash that does not match the
# published bytes fails on the user's machine, after the download.
#
# PyInstaller onefile rather than a zipapp: the target host is not
# assumed to have a Python at all, which is the whole point of the store
# delivering a binary. Cross-compilation is NOT possible — PyInstaller
# bundles the running interpreter — so this builds for the machine it
# runs on, and each platform's artifact is built on that platform.
#
# Usage: scripts/build-artifact.sh
# Output lands in dist/.

set -euo pipefail

cd "$(dirname "$0")/.."

python="${PYTHON:-.venv/bin/python}"
if [ ! -x "$python" ]; then
  echo "no interpreter at $python (set PYTHON=... to override)" >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin) goos="darwin" ;;
  Linux) goos="linux" ;;
  *) echo "unsupported platform: $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  arm64 | aarch64) goarch="arm64" ;;
  x86_64 | amd64) goarch="amd64" ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

rm -rf build dist
"$python" -m PyInstaller --onefile --clean --noconfirm \
  --name "qq-cli-${goos}-${goarch}" \
  --collect-all qqmusic_api \
  src/qq_cli/__main__.py

out="dist/qq-cli-${goos}-${goarch}"

if command -v sha256sum >/dev/null 2>&1; then
  digest="$(sha256sum "$out" | cut -d' ' -f1)"
else
  digest="$(shasum -a 256 "$out" | cut -d' ' -f1)"
fi

printf 'binary:   %s\n' "$out"
printf 'platform: %s-%s\n' "$goos" "$goarch"
printf 'sha256:   %s\n' "$digest"
