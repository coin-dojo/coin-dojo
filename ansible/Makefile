# ansible - deploy the cabinet with Ansible (ticket #90). Commands from README.md; the entry
# point stays bin/deploy, which runs Ansible in the pinned container (bin/ans; Docker/Colima only).
# Dry run (--check --diff) unless APPLY=1; APPLY=1 runs cabinet-guard first.
# ARCH/EDITION reach the playbook as extra vars coin_dojo_arch / coin_dojo_edition.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk
include $(REPO_ROOT)/mk/config.mk
include $(REPO_ROOT)/mk/cabinet.mk
include $(REPO_ROOT)/mk/edition.mk

PY_TESTS := $(sort $(wildcard $(HERE)/tests/test_*.py $(HERE)/roles/*/tests/test_*.py))

#? TAGS=a,b  only these tags/roles (comma or space separated), e.g. TAGS=dreamcast,cabinet_tweaks
#? BECOME=1  add --ask-become-pass (the pios play uses sudo)
#? LIMIT=h   inventory host: coin-dojo (default, the Debian cabinet) | coin-dojo-vm (test VM, #82)
#? W=DIR     gamelists workspace (make pull-gamelists W=DIR; deploy-collections W=DIR copies its edited gamelists)
TAGS   ?=
BECOME ?= 0
# Always a --limit, so an unlimited run can never reach more than one host.
LIMIT  ?= coin-dojo
W      ?=
HK_VALIDATED += $(call hk_valid,BECOME,0 1)
HK_VALIDATED += $(call hk_valid,LIMIT,$(HK_LIMITS))
# LIMIT=coin-dojo-vm: vm-guard (inventory check) before every run, never cabinet-guard (that one SSHes
# the cabinet). VM_INVENTORY_JSON=file feeds vm-guard a saved `ansible-inventory --list` (tests).
HK_IS_VM          := $(filter $(HK_VM_HOST),$(LIMIT))
VM_INVENTORY_JSON ?=
VM_GUARD          := $(REPO_ROOT)/vm/vm_guard.py
TAGS_ARG   := $(if $(strip $(TAGS)),--tags $(subst $(space),$(comma),$(strip $(TAGS))))
BECOME_ARG := $(if $(filter 1,$(BECOME)),--ask-become-pass)
CHECK_ARG  := $(if $(HK_APPLY),,--check --diff)
W_ARG      := $(if $(strip $(W)),-e esde_gamelists_workspace=$(abspath $(W)))
# The config renders its generated inventory + effective vars here (bin/deploy uses the same folder; mounted by bin/ans).
ANS_CFG_DIR := $(or $(ANS_CACHE),$(HK_CACHE)/ansible)/config
# Every deploy/ping/verify run takes the cabinet lane of tools/cilock.py itself (#134): agents need no wrapper.
DEPLOY     := $(CABINET_LOCK) $(HERE)/bin/deploy
ANS        := $(HERE)/bin/ans

.PHONY: test deploy ansible-ping ansible-syntax ansible-image vm-guard \
        deploy-theme deploy-collections deploy-esde esde-verify pull-gamelists deploy-launch launch-rollback

test: py-test ansible-syntax ## Structure, secrets, role tests (python3) + site.yml syntax-check (container)

deploy: $(HK_CFG_GATE) $(if $(filter community,$(EDITION)),edition-check) $(if $(HK_IS_VM),vm-guard,$(if $(HK_APPLY),cabinet-guard)) ## Run site.yml: --check --diff unless APPLY=1 (TAGS=, BECOME=1, LIMIT=, W=)
	$(Q)ANS_WORKSPACE=$(if $(strip $(W)),$(abspath $(W))) $(DEPLOY) -- $(CHECK_ARG) --limit $(LIMIT) $(ANSIBLE_EXTRA_VARS) $(W_ARG) $(TAGS_ARG) $(BECOME_ARG)

ansible-ping: $(HK_CFG_GATE) $(if $(HK_IS_VM),vm-guard) ## SSH reachability of LIMIT (bin/deploy ping)
	$(Q)DEPLOY_LIMIT=$(LIMIT) $(DEPLOY) ping

# dash (Debian's /bin/sh, the Forgejo runner) has no pipefail and EXITS on `set -o pipefail` (#135): probe it in a
# subshell. Without pipefail a failed inventory read still FAILs closed: vm_guard.py refuses empty/unreadable JSON.
vm-guard: $(HK_CFG_GATE) ## Refuse LIMIT=coin-dojo-vm if its inventory entry could be the cabinet (vm/vm_guard.py)
	$(Q)(set -o pipefail) 2>/dev/null && set -o pipefail; \
	$(if $(VM_INVENTORY_JSON),cat "$(VM_INVENTORY_JSON)",$(CONFIG_PY) render --limit $(LIMIT) --out "$(ANS_CFG_DIR)" >/dev/null \
	  && ANSIBLE_INVENTORY="$(HERE)/inventory/hosts.yml,$(HERE)/inventory/vm.yml,$(ANS_CFG_DIR)/inventory-$(LIMIT).yml" $(ANS) ansible-inventory --list 2>/dev/null) \
	  | $(PYTHON) -I $(VM_GUARD) --limit $(LIMIT)

ansible-syntax: ## site.yml --syntax-check in the pinned container (SKIP when docker is not running)
	$(Q)if docker info >/dev/null 2>&1; then \
	  $(ANS) ansible-playbook site.yml --syntax-check $(ANSIBLE_EXTRA_VARS) \
	    && echo "RESULT: PASS ansible-syntax ($$($(ANS) --image))" \
	    || { echo "RESULT: FAIL ansible-syntax"; exit 1; }; \
	else echo "RESULT: PASS ansible-syntax (SKIP: docker is not running; colima start)"; fi

ansible-image: ## Build the pinned Ansible image now (normally built on first use; heavy)
	$(Q)$(ANS) ansible --version

# --- ES-DE deploys (#89): roles esde_common, esde_theme, esde_collections, esde_verify ----------------
# Dry run unless APPLY=1 (cabinet-guard first). ES-DE restarts once at the end, only if its input changed.
deploy-theme: ## Theme: named files + _inc dirs, then personal/themes (dry run unless APPLY=1)
	$(Q)$(MAKE) --no-print-directory deploy TAGS=esde_theme

deploy-collections: ## Nested main screen (+ W=DIR edited gamelists): build, validator gate, write (dry run unless APPLY=1)
	$(Q)$(MAKE) --no-print-directory deploy TAGS=collections

deploy-esde: ## Theme + collections + verify in one run (one ES-DE restart at most)
	$(Q)$(MAKE) --no-print-directory deploy TAGS=esde

# --- launch.py (#62): role launcher. Dry run unless APPLY=1; no ES-DE restart (it runs per game) ---------
deploy-launch: ## launch.py: py_compile + argv gate, backup + launch.py.prev, install, post-check (dry run unless APPLY=1)
	$(Q)$(MAKE) --no-print-directory deploy TAGS=launcher

launch-rollback: $(HK_CFG_GATE) $(if $(HK_IS_VM),vm-guard,$(if $(HK_APPLY),cabinet-guard)) ## Put launch.py.prev back as launch.py (dry run unless APPLY=1; LIMIT=)
	$(Q)$(DEPLOY) -- $(CHECK_ARG) --limit $(LIMIT) $(ANSIBLE_EXTRA_VARS) --tags launcher -e launcher_action=rollback $(BECOME_ARG)

esde-verify: $(HK_CFG_GATE) ## Read-only: per-system counts, validator on the live nested_out, ES-DE log; FAILs on a mismatch
	$(Q)$(DEPLOY) -- --check --limit $(LIMIT) $(ANSIBLE_EXTRA_VARS) --tags esde_verify -e esde_verify_strict=true

pull-gamelists: ## Copy the cabinet's real gamelists to W/gamelists + W/pulled.md5 (read-only on the cabinet)
	$(call need,W,gamelists workspace outside git)
	$(Q)mkdir -p "$(W)/gamelists"
	$(Q)$(CABINET_SSH) 'cd ES-DE/gamelists && md5sum */gamelist.xml' > "$(W)/pulled.md5"
	$(Q)$(CABINET_SCP) -r '$(CABINET):ES-DE/gamelists/*' "$(W)/gamelists/"
	@echo "pull-gamelists: $$(wc -l < "$(W)/pulled.md5") gamelist(s) in $(W); edit there, then make deploy-collections W=$(W)"

