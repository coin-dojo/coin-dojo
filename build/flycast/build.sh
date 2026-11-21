#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build_run "A B C" lists them for the manifest)
# Build standalone flycast (SDL2 + GLES3) for the coin-dojo cabinet (Pi 5, Debian 13 arm64).
# Runs inside a pinned debian:trixie arm64 container so the libs match the cabinet.
# Usage: build.sh [--out DIR] [--help]
# Exit codes: 0 ok, 1 build failed, 2 bad usage, 3 docker missing/unusable.
set -euo pipefail

# ---- pins (change versions here only) -------------------------------------
FLYCAST_TAG="v2.7"
FLYCAST_REPO="https://github.com/flyinghead/flycast.git"
BASE_IMAGE="debian:trixie"
CMAKE_FLAGS="-DUSE_GLES=ON -DLIBRETRO=OFF -DCMAKE_BUILD_TYPE=Release"
# Every patches/*.patch must be listed here with its sha256 (applied in this order). A changed or
# unlisted patch stops the build before docker runs. Both are off unless their env var is 1 (#188):
#   0001: FLYCAST_GUI_43=1 keeps menu, settings and popups inside the centred 4:3 area (side insets)
#   0002: FLYCAST_FPS_TOPRIGHT=1 draws the FPS counter top right inside that area, cyan on indigo
#         and keeps it on while achievement popups/trackers show (they sit bottom left, #199)
FC_PATCHES="0001-gui-inside-4-3-area.patch=d34269a378227aa5bf9d7d78e6ff7b14de6e4e0afeae7833110368ca23203e31
0002-fps-top-right.patch=997535c29bf6558dae46127268c026551ddeac1c21653af71421d8dadc3160db"
# ----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"
OUT="$HERE/out"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--out DIR]

Builds standalone flycast $FLYCAST_TAG (GLES3, SDL2) in a $BASE_IMAGE linux/arm64
container and writes flycast, flycast.sha256 and BUILDINFO to DIR
(default: $OUT, gitignored). Takes ~10 min on 6 cpus. Never commit the output.
USAGE
}

build_parse_args "$@"
build_check_patches "$HERE/patches" "$FC_PATCHES"

PLATFORM="linux/arm64"
build_check_docker

# shellcheck disable=SC2119  # no argument: the --under-home check is not wanted here
build_prepare_out
build_container_name flycast

INNER=$(cat <<'INNER_EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "[1/6] deps"
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  ca-certificates build-essential cmake ninja-build git pkg-config file \
  libsdl2-dev libcurl4-openssl-dev libudev-dev libzip-dev zlib1g-dev \
  libgles-dev libegl-dev libpulse-dev libasound2-dev libminiupnpc-dev >/dev/null
echo "[2/6] source $FLYCAST_TAG"
git clone -q --depth 1 --branch "$FLYCAST_TAG" --recursive --shallow-submodules "$FLYCAST_REPO" /src
echo "[3/6] patches"
PATCH_INFO=""
while IFS='=' read -r name sum; do
  [ -n "$name" ] || continue
  [ "$(sha256sum "/patches/$name" | cut -d' ' -f1)" = "$sum" ] || { echo "error: patch $name changed" >&2; exit 1; }
  echo "patch: $name"
  git -C /src apply "/patches/$name"
  PATCH_INFO="$PATCH_INFO  $name $sum
"
done <<<"$FC_PATCHES"
echo "[4/6] configure"
# shellcheck disable=SC2086
cmake -S /src -B /build -G Ninja $CMAKE_FLAGS
echo "[5/6] build"
ninja -C /build -j"$(nproc)"
echo "[6/6] collect"
cp /build/flycast /out/flycast
bi_sha flycast
{
  echo "tag: $FLYCAST_TAG"
  echo "commit: $(git -C /src rev-parse HEAD)"
  bi_date
  echo "base_image: $BASE_IMAGE"
  echo "flags: $CMAKE_FLAGS"
  echo "patches:"
  printf '%s' "$PATCH_INFO"
  bi_file flycast
  bi_ldd flycast
} > /out/BUILDINFO
echo BUILD_OK
INNER_EOF
)

echo "building flycast $FLYCAST_TAG in $BASE_IMAGE (linux/arm64) -> $OUT"
build_run "flycast build failed" "FLYCAST_TAG FLYCAST_REPO BASE_IMAGE CMAKE_FLAGS FC_PATCHES" \
  -v "$HERE/patches:/patches:ro"
build_done flycast
