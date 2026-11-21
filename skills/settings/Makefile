# skills/settings - the SETTINGS app (#32-#35): Wi-Fi + cabinet name, stick + panel + USB keyboard.
# Tests and local PNG previews run on the laptop (cairo + FreeType from Homebrew for `preview`); the install is the
# Ansible role `settings` (make deploy TAGS=settings ...). See SKILL.md.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/../..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk
include $(REPO_ROOT)/mk/config.mk

#? OUT=file.png   preview: where the frame goes (default ~/.cache/coin-dojo/settings/coin-dojo-settings.png)
#? HOSTNAME_SHOWN=name  preview: the cabinet name shown (default hk_cabinet_hostname from the config)
#? ACTIONS=a,b    preview: inputs to replay first (up down left right ok back start esc enter bksp tab text:abc)
OUT     ?= $(HK_CACHE)/settings/coin-dojo-settings.png
HOSTNAME_SHOWN ?= $(or $(strip $(shell $(CONFIG_PY) get hk_cabinet_hostname 2>/dev/null)),coin-dojo)
ACTIONS ?=

.PHONY: test preview

test: py-test ## SETTINGS app tests (keyboard, argv-intact password, helper, no shell, brand tokens only)

preview: ## Render one frame to OUT against the fake nmcli (needs libcairo + libfreetype; no window)
	$(Q)mkdir -p "$(dir $(OUT))" && HK_FAKE_NMCLI_STATE=$(dir $(OUT))coin-dojo-fake-nmcli.json $(PYTHON) $(HERE)/settings_app.py --png "$(OUT)" \
	  --nmcli $(HERE)/fake_nmcli.py --hostname "$(HOSTNAME_SHOWN)" $(if $(strip $(ACTIONS)),--actions "$(ACTIONS)") \
	  && echo "preview: $(OUT)"
