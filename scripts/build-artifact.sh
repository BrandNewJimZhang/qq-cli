#!/usr/bin/env bash
# Build the release artifact for this platform and print its digest.
#
# The two things a marketplace entry needs are the artifact and its
# sha256, so this prints the digest and the size rather than leaving the
# publisher to remember a second command — a published hash that does
# not match the published bytes fails on the user's machine, after the
# download.
#
# ONEDIR, not onefile. Onefile re-extracts the whole bundled runtime
# into a fresh random temp directory on EVERY launch, and macOS charges
# a code-signature validation per Mach-O image the first time it is
# loaded from a given path. Fresh path every run means the cache never
# hits: measured on macOS 27 / arm64, `qq-cli --help` took 8.5 s, of
# which 0.4 s was the extraction and the rest was validation the OS
# could not reuse (11 s of wall clock against 0.34 s of CPU — the
# process was waiting, not working). The same payload built onedir runs
# in 1.26 s the first time (that is the validation, paid once at a
# stable path) and 0.06 s thereafter.
#
# The cost of that choice is that the artifact is a DIRECTORY, so it
# ships as a tar.gz whose root is `qq-cli/` and whose executable is
# `qq-cli/qq-cli` — the layout the marketplace's archive delivery
# extracts and resolves by convention, so no entry-point is declared
# anywhere. A host that cannot extract a tarball cannot install this;
# the target still needs no Python of its own, which is the property
# onefile was chosen for and this keeps.
#
# Cross-compilation is NOT possible — PyInstaller bundles the running
# interpreter — so this builds for the machine it runs on, and each
# platform's artifact is built on that platform.
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
# The bundle directory is the PROGRAM name, not the platform name: the
# marketplace extracts it under the program it delivers, and the
# platform is what the ARCHIVE is named after. Tarring the platform
# name here would make every consumer rename the directory back.
"$python" -m PyInstaller --onedir --clean --noconfirm \
  --name "qq-cli" \
  --collect-all qqmusic_api \
  src/qq_cli/__main__.py

archive="dist/qq-cli-${goos}-${goarch}.tar.gz"
# COPYFILE_DISABLE keeps macOS's AppleDouble `._*` entries out: they
# are metadata sidecars that mean nothing to the target host and would
# land as junk beside the runtime.
COPYFILE_DISABLE=1 tar -czf "$archive" -C dist qq-cli

if command -v sha256sum >/dev/null 2>&1; then
  digest="$(sha256sum "$archive" | cut -d' ' -f1)"
else
  digest="$(shasum -a 256 "$archive" | cut -d' ' -f1)"
fi
# ``wc -c`` rather than ``stat``: the flag differs between GNU and BSD,
# and this number is the size the marketplace publishes — a size that
# disagreed with the bytes would refuse the download on the user's
# machine, which is exactly what printing it here exists to prevent.
size="$(wc -c < "$archive" | tr -d ' ')"

printf 'artifact: %s\n' "$archive"
printf 'platform: %s-%s\n' "$goos" "$goarch"
printf 'sha256:   %s\n' "$digest"
printf 'size:     %s\n' "$size"
