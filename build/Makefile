# build - emulator builds from upstream source in pinned arm64 containers (tickets #90, #61).
# Commands from build/*/README.md. Output (out/, work/) is gitignored; every build writes BUILDINFO
# (source commit, flags, patch sha256s, base image digest). ARCH is checked by mk/flags.mk (arm64 only).
# retroarch + n64 feed the retroarch122 Ansible role, which pins their out/ files by sha256.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk

#? FLYCAST_OUT=DIR    flycast output dir (default build/flycast/out)
#? STELLA_OUT=DIR     Stella core output dir (default build/stella/out)
#? RETROARCH_OUT=DIR  RetroArch output dir, under $HOME (default build/retroarch/out)
#? BOX64_OUT=DIR    Box64 output dir, under $HOME (default build/box64/out)
#? FIGHTCADE_OUT=DIR fightcade downloads dir (default build/fightcade/out)
#? N64_OUT=DIR        N64 core output dir, under $HOME (default build/n64/out)
FLYCAST_OUT   ?=
STELLA_OUT    ?=
RETROARCH_OUT ?=
N64_OUT       ?=
BOX64_OUT     ?=
FIGHTCADE_OUT ?=
PY_TESTS      := $(sort $(wildcard $(HERE)/*/tests/test_*.py $(HERE)/tests/test_*.py))

# The image's eight libretro cores from pinned commits (#274; pins in build/<core>/build.sh, steps build/cores/core.sh).
CORES         := fbneo genesis_plus_gx mame mednafen_pce mednafen_saturn nestopia pcsx_rearmed snes9x

.PHONY: test all fetch flycast stella retroarch n64 box64 fightcade cores $(CORES)

test: py-test ## build.sh tests for every build here

all: flycast stella retroarch n64 ## Build everything below (one after the other)

fetch: ## Fetch the pinned RetroArch 1.22.2 + N64 core into build/*/out from the cabinet (read-only), sha256-checked (DRY=1 lists)
	$(Q)python3 $(HERE)/fetch.py $(if $(DRY),--dry-run)

flycast: ## Standalone flycast v2.7 (build/flycast/build.sh, ~10 min)
	$(Q)$(HERE)/flycast/build.sh $(if $(FLYCAST_OUT),--out "$(FLYCAST_OUT)")

stella: ## Stella 7.0 libretro core (build/stella/build.sh, ~80 s)
	$(Q)$(HERE)/stella/build.sh $(if $(STELLA_OUT),--out "$(STELLA_OUT)")

retroarch: ## RetroArch v1.22.2 + patches/, GLES/Wayland (build/retroarch/build.sh, ~6 min)
	$(Q)$(HERE)/retroarch/build.sh $(if $(RETROARCH_OUT),--out "$(RETROARCH_OUT)")

n64: ## GLES3 Mupen64Plus-Next core, rpi5_64_mesa (build/n64/build.sh, ~4 min)
	$(Q)$(HERE)/n64/build.sh $(if $(N64_OUT),--out "$(N64_OUT)")

box64: ## Box64 v0.4.4 for the Pi 5 (build/box64/build.sh, ~10 min; needs ~4 GB docker memory)
	$(Q)$(HERE)/box64/build.sh $(if $(BOX64_OUT),--out "$(BOX64_OUT)")

fightcade: ## Fetch the pinned Wine 10.0, Electron 9.1.0 and Fightcade 2.1.45 into build/fightcade/out (sha256-checked)
	$(Q)$(HERE)/fightcade/fetch.sh $(if $(FIGHTCADE_OUT),--out "$(FIGHTCADE_OUT)")

cores: ## The 8 image cores from pinned commits, one after the other (build/cores/README.md; MAME takes hours)
	$(Q)for c in $(CORES); do $(HERE)/$$c/build.sh || exit 1; done

$(CORES):
	$(Q)$(HERE)/$@/build.sh
