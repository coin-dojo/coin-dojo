#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build_run "A B C" lists them for the manifest)
# Build mupen64plus-libretro-nx (libretro N64 core, "Mupen64Plus-Next") with GLES3 for the coin-dojo
# cabinet (Pi 5, Debian 13 arm64). The Debian core asks RetroArch for desktop GL, but the Pi 5 is
# GLES-only ("Cannot use HW context"), so N64 needs this rebuild (#49). Flags = the core that runs on
# the cabinet since 2026-11-14 (platform=rpi5_64_mesa FORCE_GLES3=1, unstripped; #61, #63).
# Runs inside a digest-pinned debian:trixie arm64 container so the libs match the cabinet.
# Usage: build.sh [--out DIR] [--help]
# Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker missing/unusable.
set -euo pipefail

# ---- pins (change versions here only) -------------------------------------
MUPEN_COMMIT="15bada85906593376e8fd697f1b0b3af3a20f512"   # libretro/mupen64plus-libretro-nx develop, 2026-11-14
MUPEN_REPO="https://github.com/libretro/mupen64plus-libretro-nx.git"
# debian:trixie arm64 as of 2026-11-04 (the image the live 2026-11-14 build ran in).
BASE_IMAGE="debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6"
MAKE_FLAGS="platform=rpi5_64_mesa FORCE_GLES3=1"
# SOURCE_DATE_EPOCH for any __DATE__/__TIME__: the live build's day (UTC), so a rebuild can match it.
BUILD_EPOCH="1794657600"   # 2026-11-14T12:00:00Z
# ----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"
OUT="$HERE/out"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--out DIR]

Builds mupen64plus-libretro-nx @ ${MUPEN_COMMIT:0:12} ($MAKE_FLAGS) in a debian:trixie
linux/arm64 container and writes mupen64plus_next_libretro.so, its .sha256 and BUILDINFO to DIR
(default: $OUT, gitignored). DIR must be under \$HOME (colima only mounts \$HOME).
Never commit the output.
USAGE
}

build_parse_args "$@"

# ---- pin checks (before docker is touched) ----------------------------------
build_check_arch
build_require_commit MUPEN_COMMIT " (a branch is not reproducible)"
build_require_digest BASE_IMAGE
build_require_epoch BUILD_EPOCH
case " $MAKE_FLAGS " in *" FORCE_GLES3=1 "*) ;; *) build_fail 2 "MAKE_FLAGS must keep FORCE_GLES3=1 (the Pi 5 is GLES-only)" ;; esac

build_check_docker

build_prepare_out --under-home
build_container_name n64

build_image_id

INNER=$(cat <<'INNER_EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DEPS="ca-certificates build-essential git pkg-config file make \
  libgles-dev libegl-dev libgl-dev zlib1g-dev libpng-dev"
echo "[1/5] deps"
apt-get update -qq
# shellcheck disable=SC2086
apt-get install -y -qq --no-install-recommends $DEPS >/dev/null
echo "[2/5] source $MUPEN_COMMIT"
git init -q /src
git -C /src remote add origin "$MUPEN_REPO"
git -C /src fetch -q --depth 1 origin "$MUPEN_COMMIT"
git -C /src -c advice.detachedHead=false checkout -q FETCH_HEAD
GOT="$(git -C /src rev-parse HEAD)"
[ "$GOT" = "$MUPEN_COMMIT" ] || { echo "error: pinned commit mismatch: got $GOT, pinned $MUPEN_COMMIT" >&2; exit 1; }
# no submodule step: the tree has no .gitmodules (one stray gitlink, lightning/gnulib, is not needed)
echo "[3/5] build ($MAKE_FLAGS)"
SOURCE_DATE_EPOCH="$BUILD_EPOCH"; export SOURCE_DATE_EPOCH
# shellcheck disable=SC2086
make -C /src -j"$(nproc)" $MAKE_FLAGS >/dev/null
echo "[4/5] check + collect"
SO=/src/mupen64plus_next_libretro.so
bi_check_so "$SO"
cp "$SO" /out/mupen64plus_next_libretro.so
bi_sha mupen64plus_next_libretro.so
echo "[5/5] info"
{
  echo "commit: $GOT"
  bi_date
  echo "source_date_epoch: $SOURCE_DATE_EPOCH"
  echo "arch: $PLATFORM"
  echo "base_image: $BASE_IMAGE"
  echo "image_id: $IMAGE_ID"
  echo "flags: $MAKE_FLAGS"
  echo "gcc: $(gcc -dumpfullversion)"
  # shellcheck disable=SC2086
  bi_packages $DEPS gcc libc6-dev
  bi_file mupen64plus_next_libretro.so
  bi_sha256 mupen64plus_next_libretro.so
  bi_ldd mupen64plus_next_libretro.so
} > /out/BUILDINFO
echo BUILD_OK
INNER_EOF
)

echo "building mupen64plus-libretro-nx ${MUPEN_COMMIT:0:12} ($MAKE_FLAGS) in $BASE_IMAGE ($PLATFORM) -> $OUT"
build_run "n64 core build failed" "MUPEN_COMMIT MUPEN_REPO MAKE_FLAGS PLATFORM BUILD_EPOCH BASE_IMAGE IMAGE_ID"
build_done mupen64plus_next_libretro.so
