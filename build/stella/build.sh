#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build_run "A B C" lists them for the manifest)
# Build the Stella (Atari 2600) libretro core for the coin-dojo cabinet (Pi 5, Debian 13 arm64).
# Debian 13 ships no libretro-stella package, so we build it from the official Stella repo.
# Runs inside a pinned debian:trixie arm64 container so the libs match the cabinet.
# Usage: build.sh [--out DIR] [--help]
# Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker missing/unusable.
set -euo pipefail

# ---- pins (change versions here only) -------------------------------------
# 7.0 is the newest GitHub release; tags 7.0a-7.0c only respin the Windows installer.
STELLA_TAG="7.0"
STELLA_COMMIT="d55b1aec0d067a4c901a6dcdf81cb8f579685659"
STELLA_REPO="https://github.com/stella-emu/stella.git"
BASE_IMAGE="debian:trixie"
MAKE_FLAGS="platform=unix"
# ----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"
OUT="$HERE/out"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--out DIR]

Builds the Stella $STELLA_TAG libretro core in a $BASE_IMAGE linux/arm64 container
and writes stella_libretro.so, stella_libretro.so.sha256 and BUILDINFO to DIR
(default: $OUT, gitignored). Takes ~5 min on 6 cpus. Never commit the output.
USAGE
}

build_parse_args "$@"

# Pins must be a release tag (digits and dots, optional letter) and a full commit sha.
# A branch name such as "master" is not reproducible and is refused.
build_require_re STELLA_TAG '^[0-9]+(\.[0-9]+)+[a-z]?$' "a pinned release tag"
build_require_commit STELLA_COMMIT

PLATFORM="linux/arm64"
build_check_docker

# shellcheck disable=SC2119  # no argument: the --under-home check is not wanted here
build_prepare_out
build_container_name stella

build_image_digest

INNER=$(cat <<'INNER_EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "[1/5] deps"
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  ca-certificates build-essential git file binutils >/dev/null
echo "[2/5] source $STELLA_TAG"
git -c advice.detachedHead=false clone -q --depth 1 --branch "$STELLA_TAG" "$STELLA_REPO" /src
GOT="$(git -C /src rev-parse HEAD)"
if [ "$GOT" != "$STELLA_COMMIT" ]; then
  echo "error: tag $STELLA_TAG is $GOT, pinned $STELLA_COMMIT (tag moved?)" >&2
  exit 1
fi
echo "[3/5] build"
# shellcheck disable=SC2086
make -C /src/src/os/libretro -j"$(nproc)" $MAKE_FLAGS
echo "[4/5] check + strip"
SO=/src/src/os/libretro/stella_libretro.so
strip --strip-unneeded "$SO"
bi_check_so "$SO"
echo "[5/5] collect"
cp "$SO" /out/stella_libretro.so
bi_sha stella_libretro.so
{
  echo "tag: $STELLA_TAG"
  echo "commit: $GOT"
  bi_date
  echo "base_image: $BASE_IMAGE"
  echo "image_digest: $IMAGE_DIGEST"
  echo "gcc: $(gcc -dumpfullversion)"
  echo "flags: $MAKE_FLAGS"
  bi_file stella_libretro.so
  echo "glibc_max: $(objdump -T /out/stella_libretro.so | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)"
  echo "glibcxx_max: $(objdump -T /out/stella_libretro.so | grep -o 'GLIBCXX_[0-9.]*' | sort -uV | tail -1)"
  bi_ldd stella_libretro.so
} > /out/BUILDINFO
echo BUILD_OK
INNER_EOF
)

echo "building stella $STELLA_TAG libretro core in $BASE_IMAGE (linux/arm64) -> $OUT"
build_run "stella build failed" "STELLA_TAG STELLA_COMMIT STELLA_REPO BASE_IMAGE IMAGE_DIGEST MAKE_FLAGS"
build_done stella_libretro.so
