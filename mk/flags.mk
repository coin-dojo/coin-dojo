# mk/flags.mk - build flags shared by every coin-dojo Makefile (ticket #90).
#
#   ARCH     ?= arm64      CPU arch of the cabinet build. Only arm64 (aarch64, the Pi 5) today;
#                          x86_64 is planned: add it to HK_ARCHES once a target can build it.
#   EDITION  ?= hk_edition personal  = also personal/** (private art overlay); default from `hk_edition:` in your local config, else community
#                          community = no personal/**, nothing copyrighted; must pass
#                                      tests/test_ship_no_artifacts.py (see mk/edition.mk)
#   APPLY    ?= 0          1 = write. Targets that touch the cabinet or other hosts dry-run otherwise.
#   V        ?= 0          1 = echo every command and show full test output.
#
# Unknown values stop make before any target runs. The flags are exported, so `make -C <dir>`
# sub-makes and the scripts they call see the same values; Ansible gets them as extra vars
# (ANSIBLE_EXTRA_VARS: coin_dojo_arch, coin_dojo_edition).
#
# Works with GNU make 3.81 (what macOS ships) and newer. No 3.82+ features (.ONESHELL,
# .RECIPEPREFIX, $(file), undefine) on purpose.
ifndef HK_FLAGS_MK
HK_FLAGS_MK := 1

ifeq ($(filter 3.81 3.82 4.%,$(MAKE_VERSION)),)
$(error coin-dojo Makefiles need GNU make 3.81 or newer (found '$(MAKE_VERSION)'); run them with GNU make)
endif

# EDITION is picked before the config loader can run (the loader's local file depends on it), so the one bootstrap line
# `hk_edition: x` is read straight from the local file: $(HK_CONFIG), config/local.yml, else personal/config/local.yml.
# No such line = community (nothing copyrighted). `make EDITION=x` and the environment still win.
HK_FLAGS_DIR  := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))..)
HK_EDITION_CFG := $(firstword $(wildcard $(HK_CONFIG) $(HK_FLAGS_DIR)/config/local.yml $(HK_FLAGS_DIR)/personal/config/local.yml))
HK_EDITION_SET := $(if $(HK_EDITION_CFG),$(shell sed -n 's/^hk_edition:[[:space:]]*\([a-z]*\).*/\1/p' '$(HK_EDITION_CFG)' | head -1))
ARCH    ?= arm64
EDITION ?= $(or $(HK_EDITION_SET),community)
APPLY   ?= 0
V       ?= 0
PYTHON  ?= python3

HK_ARCHES   := arm64
HK_EDITIONS := personal community
# Ansible LIMIT hosts (ansible): the Debian cabinet and coin-dojo-vm, the
# throwaway test VM on the Mac (#82, mk/vm.mk). The VM is never the cabinet: a VM LIMIT runs
# vm-guard (vm/vm_guard.py) instead of cabinet-guard.
HK_VM_HOST  := coin-dojo-vm
HK_LIMITS   := coin-dojo $(HK_VM_HOST)

# $(call hk_valid,VAR,allowed values,hint): exactly one word, and one of the allowed values.
hk_valid = $(if $(and $(filter 1,$(words $($(1)))),$(filter $($(1)),$(2))),,$(error $(1)='$($(1))' is not valid; use one of: $(2)$(3)))
HK_VALIDATED := $(call hk_valid,ARCH,$(HK_ARCHES), (x86_64 is planned, not built yet))
HK_VALIDATED += $(call hk_valid,EDITION,$(HK_EDITIONS))
HK_VALIDATED += $(call hk_valid,APPLY,0 1)
# `make bump C=<component> V=<version>` (#278, mk/versions.mk): there V is the version, not verbosity.
HK_VALIDATED += $(if $(filter bump,$(MAKECMDGOALS)),,$(call hk_valid,V,0 1))

export ARCH EDITION APPLY V

# Derived values every Makefile can use.
HK_APPLY           := $(filter 1,$(APPLY))
APPLY_FLAG         := $(if $(HK_APPLY),--apply)
Q                  := $(if $(filter 1,$(V)),,@)
# Deployed version for the theme's build stamp: a release tag (vX.Y.Z), tag-N-gHASH between releases, the bare
# hash before the first tag; +dirty when tracked files differ from HEAD.
COIN_DOJO_VERSION   := $(or $(shell git describe --tags --always --dirty=+dirty 2>/dev/null),unknown)
ANSIBLE_EXTRA_VARS := -e coin_dojo_arch=$(ARCH) -e coin_dojo_edition=$(EDITION) -e coin_dojo_version=$(COIN_DOJO_VERSION)

empty :=
space := $(empty) $(empty)
comma := ,

# $(call need,VAR,what it is): recipe-time check that a required variable is set.
need = $(if $(strip $($(1))),,$(error $(1)= is required: $(2)))

# The ONE cache root (#142): ansible/ (bin/ans mounts it; bin/deploy's rendered config + 0600 vars file), ws/
# (workspaces, premerge trees), vm/, preview/, smoke/, shots/, settings/. Under $HOME: Colima shares only $HOME.
# `make cache-migrate` moves the old ~/.cache/coin-dojo-{ansible,preview,vm,ws} folders here.
HK_CACHE ?= $(HOME)/.cache/coin-dojo
export HK_CACHE

endif
