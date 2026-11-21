#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build/cores/core.sh)
# Build the Snes9x libretro core (snes9x) for the cabinet (Pi 5, Debian 13 arm64) from a pinned commit (#274),
# instead of the libretro buildbot's unpinned nightly. Steps shared by the eight image cores: build/cores/core.sh.
# Usage: build.sh [--out DIR] [--help]. Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker unusable.
set -euo pipefail

# ---- pins (change versions here only; the licence manifest's ref must follow) ---
CORE="snes9x"
CORE_COMMIT="204ea1c0b851f960cbf223075eb1c25641cb483c"   # libretro/snes9x master head, 2026-11-19 (the commit its licence was read at)
CORE_REPO="https://github.com/libretro/snes9x.git"
CORE_MAKE_DIR="libretro"
CORE_MAKEFILE="Makefile"
CORE_MAKE_FLAGS=""
CORE_DEPS=""
CORE_JOBS=""
CORE_MINUTES="3"
# ----------------------------------------------------------------------------------

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../cores/core.sh
source "$CORE_DIR/../cores/core.sh"
core_main "$@"
