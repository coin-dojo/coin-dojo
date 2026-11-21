#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build/cores/core.sh)
# Build the MAME (current) libretro core (mame) for the cabinet (Pi 5, Debian 13 arm64) from a pinned commit (#274),
# instead of the libretro buildbot's unpinned nightly. Steps shared by the eight image cores: build/cores/core.sh.
# MAME: 4 jobs, each compiler needs ~1.5 GB (give colima 8 GB); the longest build by far (hours).
# Usage: build.sh [--out DIR] [--help]. Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker unusable.
set -euo pipefail

# ---- pins (change versions here only; the licence manifest's ref must follow) ---
CORE="mame"
CORE_COMMIT="5f5a07748ce5c239c4118463ce8e6473743d4859"   # libretro/mame master head, 2026-11-19 (the commit its licence was read at)
CORE_REPO="https://github.com/libretro/mame.git"
CORE_MAKE_DIR=""
CORE_MAKEFILE="Makefile.libretro"
CORE_MAKE_FLAGS=""
CORE_DEPS="python3"
CORE_JOBS="4"
CORE_MINUTES="240"
# ----------------------------------------------------------------------------------

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../cores/core.sh
source "$CORE_DIR/../cores/core.sh"
core_main "$@"
