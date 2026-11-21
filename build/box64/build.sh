#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build_run "A B C" lists them for the manifest)
# Build Box64 v0.4.4 for the Pi 5 (-DRPI5ARM64) for the coin-dojo cabinet's Fightcade mode (#207, part A of #204).
# Runs inside a digest-pinned debian:trixie arm64 container so the libs match the cabinet.
# Usage: build.sh [--out DIR] [--help]
# Exit codes: 0 ok, 1 build failed, 2 bad usage / bad pin, 3 docker missing/unusable.
set -euo pipefail

# ---- pins (change versions here only) -------------------------------------
BOX64_TAG="v0.4.4"
BOX64_COMMIT="2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a"
BOX64_REPO="https://github.com/ptitSeb/box64.git"
# debian:trixie arm64 as of 2026-11-04 (same digest as build/retroarch).
BASE_IMAGE="debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6"
CMAKE_FLAGS="-DRPI5ARM64=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo"
# make -j: the #92 spike needs ~2 GB per job group; -j4 ran out of memory in a 2 GiB VM. Override with BOX64_JOBS.
BOX64_JOBS="${BOX64_JOBS:-2}"
# ----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"
OUT="$HERE/out"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--out DIR]

Builds Box64 $BOX64_TAG (${BOX64_COMMIT:0:7}) with $CMAKE_FLAGS in a debian:trixie linux/arm64
container and writes box64, box64.box64rc, x64lib/ (box64's bundled x86_64 libs), their .sha256 files
and BUILDINFO to DIR (default: $OUT, gitignored). DIR must be under \$HOME (colima only mounts \$HOME).
BOX64_JOBS=N sets make -j (default 2; needs ~2 GB RAM per job pair). Takes ~10 min on 6 cpus.
Never commit the output.
USAGE
}

build_parse_args "$@"

# ---- pin checks (before docker is touched) ----------------------------------
build_check_arch
build_require_re BOX64_TAG '^v[0-9]+(\.[0-9]+)+$' "a pinned release tag"
build_require_commit BOX64_COMMIT
build_require_digest BASE_IMAGE
build_require_re BOX64_JOBS '^[1-9][0-9]?$' "a job count (1-99)"

build_check_docker

build_prepare_out --under-home
build_container_name box64

build_image_id

INNER=$(cat <<'INNER_EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DEPS="ca-certificates build-essential cmake git python3 file"
echo "[1/5] deps"
apt-get update -qq
# shellcheck disable=SC2086
apt-get install -y -qq --no-install-recommends $DEPS >/dev/null
echo "[2/5] source $BOX64_TAG"
git -c advice.detachedHead=false clone -q --depth 1 --branch "$BOX64_TAG" "$BOX64_REPO" /src
GOT="$(git -C /src rev-parse HEAD)"
[ "$GOT" = "$BOX64_COMMIT" ] || { echo "error: tag $BOX64_TAG is $GOT, pinned $BOX64_COMMIT (tag moved?)" >&2; exit 1; }
echo "[3/5] configure"
mkdir /build
# shellcheck disable=SC2086
cd /build && cmake /src $CMAKE_FLAGS
echo "[4/5] build (make -j$BOX64_JOBS)"
make -j"$BOX64_JOBS" >/dev/null
echo "[5/5] collect"
[ -f /src/system/box64.box64rc ] || { echo "error: system/box64.box64rc missing in the source" >&2; exit 1; }
[ -d /src/x64lib ] || { echo "error: x64lib/ missing in the source" >&2; exit 1; }
rm -rf /out/x64lib
cp /build/box64 /out/box64
cp /src/system/box64.box64rc /out/box64.box64rc
cp -a /src/x64lib /out/x64lib
file -b /out/box64 | grep -q 'ELF 64-bit LSB.*ARM aarch64' || { echo "error: box64 is not an aarch64 ELF: $(file -b /out/box64)" >&2; exit 1; }
/out/box64 --version 2>&1 | tee /tmp/box64-version | head -2
grep -q "${BOX64_TAG#v}" /tmp/box64-version || { echo "error: box64 --version does not say ${BOX64_TAG#v}" >&2; exit 1; }
bi_sha box64
bi_sha box64.box64rc
(cd /out && find x64lib -type f | LC_ALL=C sort | xargs sha256sum > x64lib.sha256)
{
  echo "tag: $BOX64_TAG"
  echo "commit: $GOT"
  echo "flags: $CMAKE_FLAGS"
  echo "make_jobs: $BOX64_JOBS"
  bi_date
  echo "arch: $PLATFORM"
  echo "base_image: $BASE_IMAGE"
  echo "image_id: $IMAGE_ID"
  echo "gcc: $(gcc -dumpfullversion)"
  echo "cmake: $(cmake --version | head -1)"
  echo "version: $(head -1 /tmp/box64-version)"
  # shellcheck disable=SC2086
  bi_packages $DEPS gcc libc6-dev
  bi_file box64
  bi_sha256 box64
  echo "box64rc_sha256: $(cut -d' ' -f1 /out/box64.box64rc.sha256)"
  echo "x64lib_files: $(find /out/x64lib -type f | wc -l | tr -d ' ')"
} > /out/BUILDINFO
echo BUILD_OK
INNER_EOF
)

echo "building Box64 $BOX64_TAG (${BOX64_COMMIT:0:7}) in $BASE_IMAGE ($PLATFORM) -> $OUT"
build_run "Box64 build failed" "BOX64_TAG BOX64_COMMIT BOX64_REPO BOX64_JOBS CMAKE_FLAGS PLATFORM BASE_IMAGE IMAGE_ID" 
build_done box64
