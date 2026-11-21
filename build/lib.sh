#!/bin/bash
# Shared skeleton of build/{flycast,n64,retroarch,stella}/build.sh (#112; F4 of the #103 plan).
# Sourced, never run: each build.sh keeps its own pins, usage text, container script (INNER) and
# BUILDINFO keys; this file holds what was copied four times (arg parsing, pin checks, docker checks,
# output dir, cleanup trap, image id, patch sha checks, docker run, final sha line, and the in-container
# helpers bi_*). Exit codes stay: 1 build failed, 2 bad usage / bad pin, 3 docker missing/unusable.
# Host side uses only bash builtins + dirname/basename/cat/cut/shasum so the tests can run it with a
# PATH that has no docker.

build_fail() { local rc="$1"; shift; echo "error: $*" >&2; exit "$rc"; }

# build_parse_args "$@": sets OUT. The caller defines usage() and OUT (the default) first.
build_parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --out)
        [ $# -ge 2 ] || build_fail 2 "--out needs a directory"
        OUT="$2"; shift 2 ;;
      --out=*) OUT="${1#--out=}"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
  done
}

# ---- pin checks (before docker is touched), all exit 2 ----------------------
# build_check_arch: ARCH (default arm64) -> PLATFORM; only arm64 is built.
build_check_arch() {
  case "${ARCH:-arm64}" in
    arm64) PLATFORM="linux/arm64" ;;
    *) build_fail 2 "ARCH '${ARCH}' is not built yet (only arm64)" ;;
  esac
}
# build_require_commit VAR [SUFFIX]: VAR must be a full 40-hex commit sha.
build_require_commit() {
  local v="$1" re='^[0-9a-f]{40}$'
  [[ "${!v}" =~ $re ]] || build_fail 2 "$v '${!v}' is not a full commit sha${2:-}"
}
# build_require_digest VAR: a base image reference pinned by @sha256:.
build_require_digest() {
  local v="$1" re='@sha256:[0-9a-f]{64}$'
  [[ "${!v}" =~ $re ]] || build_fail 2 "$v '${!v}' is not pinned by digest"
}
# build_require_epoch VAR: a pinned unix time (SOURCE_DATE_EPOCH).
build_require_epoch() {
  local v="$1" re='^[0-9]{9,11}$'
  [[ "${!v}" =~ $re ]] || build_fail 2 "$v '${!v}' is not a pinned unix time"
}
# build_require_re VAR REGEX WHAT: VAR must match REGEX ("is not WHAT").
build_require_re() {
  local v="$1" re="$2"
  [[ "${!v}" =~ $re ]] || build_fail 2 "$v '${!v}' is not $3"
}

build_sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

# build_check_patches DIR PINS: every PINS line "name=sha256" must exist in DIR with that sha, and DIR
# must hold no *.patch that is not pinned. Sets PATCH_LIST (space-separated names, in order).
build_check_patches() {
  local dir="$1" pins="$2" name sum f got
  PATCH_LIST=""
  while IFS='=' read -r name sum; do
    [ -n "$name" ] || continue
    f="$dir/$name"
    [ -f "$f" ] || build_fail 2 "pinned patch missing: patches/$name"
    got="$(build_sha256_of "$f")"
    [ "$got" = "$sum" ] || build_fail 2 "patch checksum mismatch: patches/$name is $got, pinned $sum"
    PATCH_LIST="$PATCH_LIST $name"
  done <<<"$pins"
  for f in "$dir"/*.patch; do
    [ -e "$f" ] || continue
    case " $PATCH_LIST " in *" $(basename "$f") "*) ;;
      *) build_fail 2 "unpinned patch: patches/$(basename "$f") (add it to RA_PATCHES with its sha256)" ;;
    esac
  done
}

# ---- docker -----------------------------------------------------------------
# build_check_docker: exit 3 when docker is missing/unusable; warn when the host is not arm64.
build_check_docker() {
  command -v docker >/dev/null 2>&1 || build_fail 3 "docker not found"
  docker info >/dev/null 2>&1 || build_fail 3 "docker daemon not reachable (start colima)"
  case "$(docker info --format '{{.Architecture}}' 2>/dev/null)" in
    aarch64|arm64) ;;
    *) echo "warning: docker host is not arm64; building under emulation (${PLATFORM:-linux/arm64}), this will be slow" >&2 ;;
  esac
}

# build_prepare_out [--under-home]: create OUT, make it absolute; --under-home also refuses a path
# outside $HOME (colima only mounts $HOME).
build_prepare_out() {
  mkdir -p "$OUT"
  OUT="$(cd "$OUT" && pwd)"
  if [ "${1:-}" = "--under-home" ]; then
    case "$OUT" in "$HOME"/*) ;; *) build_fail 2 "--out must be under \$HOME (colima only mounts \$HOME): $OUT" ;; esac
  fi
}

# build_container_name PREFIX: NAME=PREFIX-build-PID, removed on exit.
build_container_name() {
  NAME="$1-build-$$"
  trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
}

# build_image_id: pull BASE_IMAGE when absent, set IMAGE_ID (the local image id, for BUILDINFO).
build_image_id() {
  docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || docker pull -q --platform "$PLATFORM" "$BASE_IMAGE" >/dev/null
  # shellcheck disable=SC2034  # recorded by name in the manifest (build_run lists the variable names)
  IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$BASE_IMAGE" 2>/dev/null || echo unknown)"
}
# build_image_digest: always pull a floating BASE_IMAGE tag, set IMAGE_DIGEST (RepoDigest, for BUILDINFO).
build_image_digest() {
  docker pull -q --platform "${PLATFORM:-linux/arm64}" "$BASE_IMAGE" >/dev/null
  # shellcheck disable=SC2034  # recorded by name in the manifest
  IMAGE_DIGEST="$(docker image inspect --format '{{index .RepoDigests 0}}' "$BASE_IMAGE" 2>/dev/null || echo unknown)"
}

# build_run FAILMSG "ENV NAMES" [extra docker args]: run $INNER in $BASE_IMAGE with each named variable
# passed as -e NAME=value, $OUT mounted at /out and the in-container helpers (BUILD_INNER_LIB) prepended.
# A failed run prints "error: FAILMSG" and exits 1.
build_run() {
  local msg="$1" names="$2" v
  local -a args=()
  shift 2
  for v in $names; do args+=(-e "$v=${!v}"); done
  if ! docker run --name "$NAME" --platform "${PLATFORM:-linux/arm64}" "${args[@]}" \
      -v "$OUT:/out" "$@" "$BASE_IMAGE" bash -c "$BUILD_INNER_LIB"$'\n'"$INNER"; then
    build_fail 1 "$msg"
  fi
}

# build_done FILE: the closing lines every build prints.
build_done() {
  echo "done: $OUT/$1"
  cat "$OUT/$1.sha256"
}

# ---- in-container helpers (prepended to each build's INNER script) ----------
# Each writes one BUILDINFO key (or block) exactly as the four scripts did; the key ORDER stays in each
# build.sh (see build/tests/test_lib.py, which pins it).
BUILD_INNER_LIB=$(cat <<'INNER_LIB_EOF'
# bi_check_so SO: must be an aarch64 ELF shared object exporting the libretro entry points.
bi_check_so() {
  file -b "$1" | grep -q 'ELF 64-bit LSB shared object, ARM aarch64' \
    || { echo "error: not an aarch64 shared object: $(file -b "$1")" >&2; exit 1; }
  local sym
  for sym in retro_init retro_load_game retro_run retro_api_version; do
    nm -D --defined-only "$1" | grep -qw "$sym" \
      || { echo "error: $sym not exported" >&2; exit 1; }
  done
}
# bi_sha NAME: write /out/NAME.sha256.
bi_sha() { (cd /out && sha256sum "$1" > "$1.sha256"); }
bi_date() { echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; }
bi_file() { echo "file: $(file -b "/out/$1")"; }
bi_sha256() { echo "sha256: $(cut -d' ' -f1 "/out/$1.sha256")"; }
bi_ldd() { echo "ldd:"; ldd "/out/$1" | sed 's/^[[:space:]]*/  /'; }
# bi_packages PKG...: installed versions, sorted.
bi_packages() { echo "packages:"; dpkg-query -W -f '  ${Package} ${Version}\n' "$@" 2>/dev/null | sort -u; }
INNER_LIB_EOF
)
