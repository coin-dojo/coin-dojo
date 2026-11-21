# coin-dojo - single entry point (ticket #90). `make help` lists everything.
#
# Each area has its own Makefile with the commands its SKILL.md/README documents; this one calls
# them with `make -C <dir> <target>` and passes ARCH/EDITION/APPLY/V down. Shared rules and flags
# live in mk/*.mk (flags, help, python tests, cabinet, edition, vm = the test VM). GNU make 3.81+.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(HERE)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/config.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk
include $(REPO_ROOT)/mk/edition.mk
include $(REPO_ROOT)/mk/vm.mk
include $(REPO_ROOT)/mk/smoke.mk
include $(REPO_ROOT)/mk/lint.mk
include $(REPO_ROOT)/mk/ci.mk
include $(REPO_ROOT)/mk/versions.mk

# Areas with a Makefile; `make test` runs each one's `test`.
SUBDIRS := ansible skills/collections skills/badges skills/controls skills/local-preview \
           skills/build-image skills/settings brand build
MAKEFLAGS += --no-print-directory

# Repo-wide tests that no area Makefile runs (every tracked test_*.py runs exactly once;
# mk/tests/test_make.py checks that).
PY_TESTS := $(addprefix $(REPO_ROOT)/,) \
            $(sort $(wildcard $(REPO_ROOT)/docs/tests/test_*.py $(REPO_ROOT)/tests/test_*.py $(REPO_ROOT)/mk/tests/test_*.py $(REPO_ROOT)/themes/tests/test_*.py \
                                $(REPO_ROOT)/tools/tests/test_*.py $(REPO_ROOT)/vm/tests/test_*.py $(REPO_ROOT)/tests/smoke/test_*.py))

#? hk_<name>=v  any target: override a config variable (make config hk_cabinet_uid=1002); LIMIT=coin-dojo-vm shows the VM's
#? TAGS=a,b  deploy: only these Ansible tags (roles)
#? BECOME=1  deploy: add --ask-become-pass (pios play needs sudo)
#? W=DIR    deploy-collections: gamelists workspace (make -C ansible pull-gamelists W=DIR)

.PHONY: backup-config build-fetch commit hooks test test-run test-list-all preview image base-image build cache-migrate

commit: ## Guided Conventional Commit (gum): type, scope, #ticket, summary, description; refuses with nothing staged
	$(Q)$(REPO_ROOT)/tools/commit.sh

hooks: ## Install the commit-msg hook (git config core.hooksPath .githooks; worktrees share it)
	$(Q)git -C "$(REPO_ROOT)" config core.hooksPath .githooks && echo "hooks: core.hooksPath = .githooks"

test: ## Run every test: each area's `make test`, then the repo-wide ones (takes the heavy CI lock, #119)
	$(Q)$(REPO_ROOT)/tools/cilock.py run heavy -- $(MAKE) test-run

test-run:
	$(Q)fails=; for d in $(SUBDIRS); do \
	  echo "== $$d"; $(MAKE) -C $$d test || fails="$$fails $$d"; \
	done; \
	echo "== repo-wide"; $(MAKE) py-test || fails="$$fails repo-wide"; \
	if [ -n "$$fails" ]; then echo "RESULT: FAIL make test:$$fails"; exit 1; fi; \
	echo "RESULT: PASS make test"

test-list-all: ## Print every test file `make test` runs (all areas)
	$(Q)for d in $(SUBDIRS); do $(MAKE) -C $$d py-list || exit 1; done; $(MAKE) py-list

preview: ## Local ES-DE theme preview (skills/local-preview esde; PREVIEW_OUT=, VIEW=)
	$(Q)$(MAKE) -C skills/local-preview esde

# Forwarded to ansible/Makefile (#142): `make help` shows ansible's `##` text for each, so it has one source.
# The rule's target list must equal HK_FWD_ansible (mk/tests/test_make.py checks it).
HK_FWD_DIRS    := ansible
HK_FWD_ansible := deploy deploy-theme deploy-collections deploy-esde esde-verify launch-rollback deploy-launch

.PHONY: $(HK_FWD_ansible)
deploy deploy-theme deploy-collections deploy-esde esde-verify launch-rollback deploy-launch:
	$(Q)$(MAKE) -C $(REPO_ROOT)/ansible $@

cache-migrate: ## Move the old ~/.cache/coin-dojo-{ansible,preview,vm,ws} folders under HK_CACHE (~/.cache/coin-dojo); dry run unless APPLY=1
	$(Q)APPLY=$(APPLY) HK_CACHE="$(HK_CACHE)" sh $(REPO_ROOT)/tools/cache-migrate.sh

# --- operator commands (#143) ---------------------------------------------------------------------------------------
#? SMOKE=1  rollout: also run smoke-launch at the end
SMOKE ?= 0
HK_VALIDATED += $(call hk_valid,SMOKE,0 1)
HK_LOCAL_CFG  = $(firstword $(wildcard $(HK_CONFIG) $(REPO_ROOT)/config/local.yml $(if $(filter personal,$(EDITION)),$(REPO_ROOT)/personal/config/local.yml)))
SETUP_LOCAL  ?= $(REPO_ROOT)/config/local.yml
ROLLOUT_MAKE ?= $(MAKE) --no-print-directory -C $(REPO_ROOT)
ROLLOUT_RECAP ?= $(REPO_ROOT)/tools/recap.py
.PHONY: setup setup-config doctor rollout

setup: ## One-time: venv ~/.venvs/coin-dojo (PyYAML + Pillow), the commit hook, config/local.yml from the example when you have no local config
	$(Q)$(MAKE) --no-print-directory config-venv
	$(Q)"$(CONFIG_VENV)/bin/python" -c 'import PIL' 2>/dev/null || "$(CONFIG_VENV)/bin/python" -m pip install -q pillow
	$(Q)$(MAKE) --no-print-directory hooks
	$(Q)$(MAKE) --no-print-directory setup-config
	@echo "setup: done; next: edit your local config (hk_cabinet_host), then make doctor"

setup-config: ## Copy config/local.example.yml to config/local.yml unless a local config exists (never overwrites)
	$(Q)if [ -n "$(HK_CONFIG)" ] || [ -n "$(HK_LOCAL_CFG)" ]; then echo "setup-config: local config is $(or $(HK_LOCAL_CFG),$(HK_CONFIG)) (left as is)"; \
	elif [ -e "$(SETUP_LOCAL)" ]; then echo "setup-config: $(SETUP_LOCAL) exists (left as is)"; \
	else cp "$(REPO_ROOT)/config/local.example.yml" "$(SETUP_LOCAL)" && echo "setup-config: wrote $(SETUP_LOCAL); set hk_cabinet_host in it"; fi

doctor: ## Check this Mac: python, venv, config resolves, ssh key, docker/colima, limactl, gum, Keychain item present (never its value)
	$(Q)HK_REPO="$(REPO_ROOT)" CONFIG_PYTHON="$(CONFIG_PYTHON)" CFG_LIMIT="$(CFG_LIMIT)" bash $(REPO_ROOT)/tools/doctor.sh

rollout: $(HK_CFG_GATE) ## Dry run; APPLY=1: + apply, 2nd dry run must recap changed=0 (tools/recap.py), esde-verify, SMOKE=1 smoke; logs in HK_CACHE/rollout
	$(Q)APPLY=$(APPLY) SMOKE=$(SMOKE) ROLLOUT_MAKE="$(ROLLOUT_MAKE)" ROLLOUT_RECAP="$(ROLLOUT_RECAP)" bash $(REPO_ROOT)/tools/rollout.sh








# Paths are made absolute here: make -C changes the directory (#237).
HK_IMG_PATHS = $(foreach v,BASE_IMG IMAGE_OUT CONFIG,$(if $($(v)),$(v)=$(abspath $($(v)))))
image: ## Build the OS image (skills/build-image image; BASE_IMG=, IMAGE_OUT=, CONFIG=, MIN_CARD_GB=)
	$(Q)$(MAKE) -C skills/build-image image $(HK_IMG_PATHS)

gh-release: ## Publish the strict community image + its sources as a GitHub Release (VERSION=, IMG=; dry run; APPLY=1 draft; PUBLISH=1 un-drafts, #287)
	$(Q)$(MAKE) -C skills/build-image gh-release VERSION="$(VERSION)" PUBLISH="$(PUBLISH)" $(if $(IMG),IMG=$(abspath $(IMG)))

base-image: ## Fetch the official Raspberry Pi OS Lite arm64 image, sha256-pinned (BASE_DIR=; prints BASE_IMG=)
	$(Q)$(MAKE) -C skills/build-image base-image $(if $(BASE_DIR),BASE_DIR=$(abspath $(BASE_DIR)))

backup-config: $(HK_CFG_GATE) ## Pull the cabinet's config allowlist (read-only) into personal/backup/<host>/, secrets REDACTED; dry run unless APPLY=1; never commits (#286)
	$(Q)$(PYTHON) $(REPO_ROOT)/build/backup_config.py $(APPLY_FLAG)

build-fetch: ## Fetch the pinned RetroArch + N64 build outputs from the cabinet (read-only, sha256-checked) for the VM role (DRY=1)
	$(Q)$(MAKE) -C build fetch DRY=$(DRY)

build: ## Build flycast + the Stella core in arm64 containers (build all)
	$(Q)$(MAKE) -C build all
