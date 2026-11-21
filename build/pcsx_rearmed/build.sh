#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name (build/cores/core.sh)
# Build the PCSX-ReARMed libretro core (pcsx_rearmed) for the cabinet (Pi 5, Debian 13 arm64) from a pinned commit (#274),
# instead of the libretro buildbot's unpinned nightly. Steps shared by the eight image cores: build/cores/core.sh.
# Has git submodules (libpicofe, lightrec, ...): core.sh checks them out at the commits this tree records.
# Usage: build.sh [--out DIR] [--help]. Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker unusable.
set -euo pipefail

# ---- pins (change versions here only; the licence manifest's ref must follow) ---
CORE="pcsx_rearmed"
CORE_COMMIT="7b0e8f8fadfbce3d170d195b9d9eecaabfa08c8e"   # libretro/pcsx_rearmed master head, 2026-11-19 (the commit its licence was read at)
CORE_REPO="https://github.com/libretro/pcsx_rearmed.git"
CORE_MAKE_DIR=""
CORE_MAKEFILE="Makefile.libretro"
CORE_MAKE_FLAGS=""
CORE_DEPS=""
CORE_JOBS=""
CORE_MINUTES="4"
# ----------------------------------------------------------------------------------

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../cores/core.sh
source "$CORE_DIR/../cores/core.sh"
core_main "$@"
