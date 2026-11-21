#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build_run "A B C" lists them for the container)
# Shared builder for the image's plain-make libretro cores (#274): build/<core>/build.sh sets its pins and sources
# this file, which sources build/lib.sh. Sourced, never run. One build = one pinned commit of one core, built in the
# digest-pinned debian:trixie arm64 container (the cabinet's libs), stripped, checked (aarch64 ELF, libretro entry
# points), and written as <core>_libretro.so + .sha256 + BUILDINFO to build/<core>/out (gitignored).
# Exit codes (build/lib.sh): 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker missing/unusable.
#
# The caller sets, before sourcing (all plain strings, checked below before docker is touched):
#   CORE             bare core name; the folder name and <CORE>_libretro.so
#   CORE_COMMIT      full 40-hex commit (a branch is not reproducible); licences.py reads it as the exact source ref
#   CORE_REPO        https://github.com/libretro/<repo>.git
#   CORE_MAKE_DIR    folder (in the tree) make runs in and the .so lands in; "" = the top
#   CORE_MAKEFILE    the makefile there (Makefile, Makefile.libretro)
#   CORE_MAKE_FLAGS  make variables (NAME=value words only)
#   CORE_DEPS        extra apt packages ("" = none)
#   CORE_JOBS        make -j ("" = nproc; MAME needs fewer for RAM)
#   CORE_MINUTES     rough build time on colima with 6 cpus (shown in --help)

# ---- shared pins (change here only) -------------------------------------------
# debian:trixie arm64 as of 2026-11-04, the image build/n64 builds in (the cabinet runs Debian 13).
BASE_IMAGE="debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6"
CORE_BASE_DEPS="ca-certificates build-essential git file binutils pkg-config zlib1g-dev"
# -------------------------------------------------------------------------------

CORES_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$CORES_LIB_DIR/../lib.sh"

core_usage() {
  cat <<USAGE
Usage: $(basename "$0") [--out DIR]

Builds the $CORE libretro core at ${CORE_COMMIT:0:12} ($CORE_REPO) in a debian:trixie linux/arm64
container and writes ${CORE}_libretro.so, its .sha256 and BUILDINFO to DIR (default: $OUT, gitignored).
DIR must be under \$HOME (colima only mounts \$HOME). Takes ~$CORE_MINUTES min on 6 cpus. Never commit the output.
USAGE
}

# core_check_pins: every pin before docker is touched (exit 2).
core_check_pins() {
  build_check_arch
  build_require_re CORE '^[a-z0-9_]+$' "a bare core name"
  [ "$CORE" = "$(basename "$CORE_DIR")" ] || build_fail 2 "CORE '$CORE' is not this folder's name ($(basename "$CORE_DIR"))"
  build_require_commit CORE_COMMIT " (a branch is not reproducible)"
  build_require_re CORE_REPO '^https://github\.com/libretro/[A-Za-z0-9_.-]+\.git$' "an https://github.com/libretro/<repo>.git URL"
  build_require_re CORE_MAKE_DIR '^([A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*)?$' "a relative folder in the tree"
  build_require_re CORE_MAKEFILE '^[A-Za-z0-9_][A-Za-z0-9_.-]*$' "a makefile name"
  build_require_re CORE_MAKE_FLAGS '^[A-Za-z0-9_=.,/+ -]*$' "NAME=value words"
  local w
  for w in $CORE_MAKE_FLAGS; do
    [[ "$w" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || build_fail 2 "CORE_MAKE_FLAGS word '$w' is not NAME=value"
  done
  build_require_re CORE_DEPS '^[a-z0-9.+ -]*$' "apt package names"
  build_require_re CORE_JOBS '^([1-9][0-9]?)?$' "empty or a job count"
  build_require_digest BASE_IMAGE
}

# core_main "$@": the whole build. The caller sets the pins above and CORE_DIR (its own folder).
core_main() {
  OUT="$CORE_DIR/out"
  usage() { core_usage; }
  build_parse_args "$@"
  core_check_pins
  build_check_docker
  build_prepare_out --under-home
  build_container_name "core-$CORE"
  build_image_id

  INNER=$(cat <<'INNER_EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DEPS="$CORE_BASE_DEPS $CORE_DEPS"
SO_NAME="${CORE}_libretro.so"
TREE=/src${CORE_MAKE_DIR:+/$CORE_MAKE_DIR}
echo "[1/5] deps"
apt-get update -qq
# shellcheck disable=SC2086
apt-get install -y -qq --no-install-recommends $DEPS >/dev/null
echo "[2/5] source $CORE_COMMIT"
git init -q /src
git -C /src remote add origin "$CORE_REPO"
git -C /src fetch -q --depth 1 origin "$CORE_COMMIT"
git -C /src -c advice.detachedHead=false checkout -q FETCH_HEAD
GOT="$(git -C /src rev-parse HEAD)"
[ "$GOT" = "$CORE_COMMIT" ] || { echo "error: pinned commit mismatch: got $GOT, pinned $CORE_COMMIT" >&2; exit 1; }
# submodules (pcsx_rearmed): at the commits the pinned tree records, listed in BUILDINFO
if [ -f /src/.gitmodules ]; then
  git -C /src submodule update -q --init --recursive
fi
SUBMODULES="$(git -C /src submodule status --recursive 2>/dev/null | sed 's/^[ +-]//' | cut -d' ' -f1,2)"
echo "[3/5] build ($CORE_MAKEFILE $CORE_MAKE_FLAGS)"
# the commit's own time, so __DATE__/__TIME__ do not depend on the day of the build
SOURCE_DATE_EPOCH="$(git -C /src log -1 --format=%ct)"; export SOURCE_DATE_EPOCH
JOBS="${CORE_JOBS:-$(nproc)}"
# shellcheck disable=SC2086
if ! make -C "$TREE" -f "$CORE_MAKEFILE" -j"$JOBS" $CORE_MAKE_FLAGS >/tmp/make.log 2>&1; then
  tail -40 /tmp/make.log >&2
  echo "error: make failed (last 40 lines above)" >&2
  exit 1
fi
echo "[4/5] check + strip"
SO="$TREE/$SO_NAME"
[ -f "$SO" ] || { echo "error: make wrote no $SO" >&2; exit 1; }
strip --strip-unneeded "$SO"
bi_check_so "$SO"
cp "$SO" "/out/$SO_NAME"
bi_sha "$SO_NAME"
echo "[5/5] info"
{
  echo "core: $CORE"
  echo "commit: $GOT"
  echo "repo: $CORE_REPO"
  echo "submodules:"
  if [ -n "$SUBMODULES" ]; then echo "$SUBMODULES" | sed 's/^/  /'; fi
  bi_date
  echo "source_date_epoch: $SOURCE_DATE_EPOCH"
  echo "arch: $PLATFORM"
  echo "base_image: $BASE_IMAGE"
  echo "image_id: $IMAGE_ID"
  echo "make: ${CORE_MAKE_DIR:-.}/$CORE_MAKEFILE"
  echo "flags: $CORE_MAKE_FLAGS"
  echo "jobs: $JOBS"
  echo "gcc: $(gcc -dumpfullversion)"
  # shellcheck disable=SC2086
  bi_packages $DEPS gcc libc6-dev
  bi_file "$SO_NAME"
  bi_sha256 "$SO_NAME"
  echo "glibc_max: $(objdump -T "/out/$SO_NAME" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)"
  bi_ldd "$SO_NAME"
} > /out/BUILDINFO
echo BUILD_OK
INNER_EOF
)

  echo "building $CORE ${CORE_COMMIT:0:12} in $BASE_IMAGE ($PLATFORM) -> $OUT"
  build_run "$CORE core build failed" \
    "CORE CORE_COMMIT CORE_REPO CORE_MAKE_DIR CORE_MAKEFILE CORE_MAKE_FLAGS CORE_DEPS CORE_JOBS CORE_BASE_DEPS PLATFORM BASE_IMAGE IMAGE_ID"
  build_done "${CORE}_libretro.so"
}
