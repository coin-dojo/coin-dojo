#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build/cores/core.sh)
# Build the Nestopia UE libretro core (nestopia) for the cabinet (Pi 5, Debian 13 arm64) from a pinned commit (#274),
# instead of the libretro buildbot's unpinned nightly. Steps shared by the eight image cores: build/cores/core.sh.
# Usage: build.sh [--out DIR] [--help]. Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker unusable.
set -euo pipefail

# ---- pins (change versions here only; the licence manifest's ref must follow) ---
CORE="nestopia"
CORE_COMMIT="f1e02d12664834536edbf7352995a3d0cd1caef4"   # libretro/nestopia master head, 2026-11-19 (the commit its licence was read at)
CORE_REPO="https://github.com/libretro/nestopia.git"
CORE_MAKE_DIR="libretro"
CORE_MAKEFILE="Makefile"
CORE_MAKE_FLAGS=""
CORE_DEPS=""
CORE_JOBS=""
CORE_MINUTES="2"
# ----------------------------------------------------------------------------------

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../cores/core.sh
source "$CORE_DIR/../cores/core.sh"
core_main "$@"
