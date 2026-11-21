# mk/config.mk - configuration: make config-check / config / config-docs / config-venv (ticket #118, C1 of #103).
#
# Variables are declared in config/variables.d/<area>.yml; precedence default < config/targets/<target>.yml < local
# (config/local.yml, else personal/config/local.yml for EDITION=personal; $(HK_CONFIG) overrides) < command line.
# tools/config.py is the one loader (docs/CONFIG.md is its generated reference). Needs PyYAML: make config-venv.
#
#   CONFIG_PYTHON  python with PyYAML (default: ~/.venvs/coin-dojo/bin/python when it exists, else python3)
#   hk_<name>=v    command line override of any variable (make deploy hk_cabinet_uid=1002); same as HK_SET="hk_a=1 hk_b=2"
#   HK_CONFIG=f    use this local file instead of config/local.yml / personal/config/local.yml
#
# Every target that reaches the cabinet or the VM lists $(HK_CFG_GATE) first, so a bad config stops it before anything runs.
ifndef HK_CONFIG_MK
HK_CONFIG_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk

CONFIG_VENV   ?= $(HOME)/.venvs/coin-dojo
CONFIG_PYTHON ?= $(firstword $(wildcard $(CONFIG_VENV)/bin/python) $(PYTHON))
CONFIG_PY      = $(CONFIG_PYTHON) $(REPO_ROOT)/tools/config.py
CFG_LIMIT      = $(or $(LIMIT),coin-dojo)
# The one shared gate: add it as the FIRST prerequisite of every deploy / vm / smoke / cabinet target.
HK_CFG_GATE   := config-check

# Command-line `hk_x=v` variables reach config.py (and, through bin/deploy, Ansible) as HK_SET.
HK_SET := $(strip $(foreach v,$(filter hk_%,$(.VARIABLES)),$(if $(filter command line,$(origin $(v))),$(v)=$($(v)))))
export HK_SET

# (its `#?` flag lines live in the root Makefile: this file is included several times, and make help lists each copy)

.PHONY: config-check config config-docs config-venv
config-check: ## Validate the configuration (unknown name with did-you-mean, type, range, missing host); runs before every deploy
	$(Q)$(CONFIG_PY) check --limit $(CFG_LIMIT)

config: ## Show every effective variable and where its value comes from (LIMIT=coin-dojo-vm for the VM)
	$(Q)$(CONFIG_PY) show --limit $(CFG_LIMIT)

config-docs: ## Regenerate docs/CONFIG.md from config/variables.d/*.yml (deterministic)
	$(Q)$(CONFIG_PY) docs

config-venv: ## One-time: create the PyYAML venv CONFIG_VENV (~/.venvs/coin-dojo) the config loader needs
	$(Q)test -x "$(CONFIG_VENV)/bin/python" || $(PYTHON) -m venv "$(CONFIG_VENV)"
	$(Q)"$(CONFIG_VENV)/bin/python" -c 'import yaml' 2>/dev/null || "$(CONFIG_VENV)/bin/python" -m pip install -q --require-hashes -r "$(REPO_ROOT)/mk/config-requirements.txt"
	@echo "config-venv: $(CONFIG_VENV) ready (PyYAML installed)"

endif
