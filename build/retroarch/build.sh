#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build_run "A B C" lists them for the manifest)
# Build RetroArch 1.22.2 (GLES, Wayland, KMS, udev; no X11/Vulkan/Qt) for the coin-dojo cabinet
# (Pi 5, Debian 13 arm64). The cabinet runs this binary for ALL RetroArch systems (#61, #62, #63).
# Why: Debian's RetroArch 1.20.0 + Raspberry Pi's libwayland-client 1.24 deadlock in flush_wayland_fd()
# (blocking wl_display_dispatch; RetroArch #18148, fixed by 6775559c2 in 1.22.0). N64 (GLideN64) hit it.
# Runs inside a digest-pinned debian:trixie arm64 container so the libs match the cabinet.
# Usage: build.sh [--out DIR] [--help]
# Exit codes: 0 ok, 1 build failed, 2 bad usage / bad pin / patch checksum mismatch, 3 docker missing.
set -euo pipefail

# ---- pins (change versions here only) -------------------------------------
RA_TAG="v1.22.2"
RA_COMMIT="69a4f0ea1e8aaf442ae4858f2e7f2b31a1776576"
RA_REPO="https://github.com/libretro/RetroArch.git"
# debian:trixie arm64 as of 2026-11-04 (the image the live 2026-11-14 build ran in).
BASE_IMAGE="debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6"
# __DATE__ in the binary ("Built: Nov 14 2026"). Pinned to the live build's day (UTC) so a rebuild is
# bit-identical to the cabinet's file (the BuildID hashes the content, so it then matches too).
BUILD_EPOCH="1794657600"   # 2026-11-14T12:00:00Z
CONFIGURE_FLAGS="--prefix=/usr --disable-x11 --disable-qt --disable-vulkan --disable-ffmpeg --disable-discord \
 --enable-wayland --enable-egl --enable-kms --enable-opengles --enable-opengles3 --enable-opengles3_1 \
 --enable-udev --enable-alsa --enable-pulse --enable-zlib --enable-freetype"
# Every patches/*.patch must be listed here with its sha256 (applied in this order). A changed or
# unlisted patch stops the build before docker runs.
#   0001: only loop over the configs eglChooseConfig filled (GLES2 path segfault, Pi 5 v3d / Wayland)
#   0002: an --enable-opengles3 build has no HAVE_OPENGLES2; serve GLES2 requests with the ES3 attribs
RA_PATCHES="0001-egl-loop-only-matched-configs.patch=1db8a02d7081729e804c83960504a5d222c46711f07bc4fd6fb0194e7199b422
0002-wayland-gles2-request-on-gles3-build.patch=1a80154359f82f36df6aac4c4481c914f9eb897dd1f8a813acc06780d22b836e"
# ----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"
OUT="$HERE/out"
RA_STRIP="${RA_STRIP:-1}"   # 1 = strip (what the cabinet runs); 0 keeps symbols for gdb

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--out DIR]

Builds RetroArch $RA_TAG (${RA_COMMIT:0:7}) + patches/ in a debian:trixie linux/arm64 container
and writes retroarch, retroarch.sha256 and BUILDINFO to DIR (default: $OUT, gitignored).
DIR must be under \$HOME (colima only mounts \$HOME). RA_STRIP=0 keeps symbols.
Takes ~6 min on 6 cpus. Never commit the output.
USAGE
}

build_parse_args "$@"

# ---- pin checks (before docker is touched) ----------------------------------
build_check_arch
build_require_re RA_TAG '^v[0-9]+(\.[0-9]+)+$' "a pinned release tag"
build_require_commit RA_COMMIT
build_require_digest BASE_IMAGE
build_require_epoch BUILD_EPOCH
case "$RA_STRIP" in 0|1) ;; *) build_fail 2 "RA_STRIP must be 0 or 1" ;; esac
build_check_patches "$HERE/patches" "$RA_PATCHES"

build_check_docker

build_prepare_out --under-home
build_container_name retroarch

build_image_id

INNER=$(cat <<'INNER_EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DEPS="ca-certificates build-essential git pkg-config file \
  libgles-dev libegl-dev libgbm-dev libdrm-dev libwayland-dev wayland-protocols libwayland-bin libxkbcommon-dev \
  libudev-dev libasound2-dev libpulse-dev zlib1g-dev libfreetype-dev"
echo "[1/5] deps"
apt-get update -qq
# shellcheck disable=SC2086
apt-get install -y -qq --no-install-recommends $DEPS >/dev/null
echo "[2/5] source $RA_TAG"
git -c advice.detachedHead=false clone -q --depth 1 --branch "$RA_TAG" "$RA_REPO" /src
GOT="$(git -C /src rev-parse HEAD)"
[ "$GOT" = "$RA_COMMIT" ] || { echo "error: tag $RA_TAG is $GOT, pinned $RA_COMMIT (tag moved?)" >&2; exit 1; }
echo "[3/5] patches"
PATCH_INFO=""
while IFS='=' read -r name sum; do
  [ -n "$name" ] || continue
  [ "$(sha256sum "/patches/$name" | cut -d' ' -f1)" = "$sum" ] || { echo "error: patch $name changed" >&2; exit 1; }
  echo "patch: $name"
  git -C /src apply "/patches/$name"
  PATCH_INFO="$PATCH_INFO  $name $sum
"
done <<<"$RA_PATCHES"
echo "[4/5] build"
# Reproducible __DATE__/__TIME__: the pinned BUILD_EPOCH, not the wall clock.
SOURCE_DATE_EPOCH="$BUILD_EPOCH"; export SOURCE_DATE_EPOCH
# shellcheck disable=SC2086
cd /src && ./configure $CONFIGURE_FLAGS && make -j"$(nproc)" >/dev/null
echo "[5/5] collect"
cp /src/retroarch /out/retroarch
if [ "$RA_STRIP" = 1 ]; then strip /out/retroarch; fi
bi_sha retroarch
{
  echo "tag: $RA_TAG"
  echo "commit: $GOT"
  echo "patches:"
  printf '%s' "$PATCH_INFO"
  echo "flags: $CONFIGURE_FLAGS"
  echo "strip: $RA_STRIP"
  echo "source_date_epoch: $SOURCE_DATE_EPOCH"
  bi_date
  echo "arch: $PLATFORM"
  echo "base_image: $BASE_IMAGE"
  echo "image_id: $IMAGE_ID"
  echo "gcc: $(gcc -dumpfullversion)"
  # shellcheck disable=SC2086
  bi_packages $DEPS gcc libc6-dev libwayland-client0
  bi_file retroarch
  bi_sha256 retroarch
} > /out/BUILDINFO
echo BUILD_OK
INNER_EOF
)

echo "building RetroArch $RA_TAG (${RA_COMMIT:0:7}) +$(echo "$PATCH_LIST" | wc -w | tr -d ' ') patches in $BASE_IMAGE ($PLATFORM) -> $OUT"
build_run "RetroArch build failed" "RA_TAG RA_COMMIT RA_REPO RA_STRIP RA_PATCHES CONFIGURE_FLAGS PLATFORM BUILD_EPOCH BASE_IMAGE IMAGE_ID" \
  -v "$HERE/patches:/patches:ro"
build_done retroarch
