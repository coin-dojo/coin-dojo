# skills/local-preview - run the cabinet's front end on the Mac (ticket #90). Commands from SKILL.md.
# Needs Colima (docker); arm64 only. Heavy: one preview at a time.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/../..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk

#? THEME=DIR        theme to render (default themes/coin-dojo; must be under $HOME)
#? PREVIEW_OUT=DIR  screenshots go here (default ~/.cache/coin-dojo/preview/out)
#? VIEW=v           system | gamelist | both (default both)
#? KEYS="..."       xdotool keys before the gamelist shot, e.g. KEYS="Left Return Return"
#? FIXTURE=DIR      gamelists/collections fixture (default: skills/local-preview/fixtures/esde-preview)
THEME       ?= $(REPO_ROOT)/themes/coin-dojo
PREVIEW_OUT ?= $(HK_CACHE)/preview/out
# The preview scripts' cache (AppImage, extracted tree, images; #142: under the one cache root).
COIN_DOJO_PREVIEW_CACHE ?= $(HK_CACHE)/preview
export COIN_DOJO_PREVIEW_CACHE
VIEW        ?= both
KEYS        ?=
FIXTURE     ?=
PY_TESTS    := $(HERE)/tests/test_esde_preview.py $(HERE)/tests/test_ra122_preview.py

.PHONY: test smoke esde esde-system esde-gamelist

test: py-test ## Fast checks of the ES-DE and RetroArch 1.22 preview wrappers (~1 s, no docker)

smoke: ## Full ES-DE render smoke: good theme PASS, malformed copy MUST FAIL (docker, slow)
	$(Q)cd "$(REPO_ROOT)" && ESDE_PREVIEW_SMOKE=1 $(PYTHON) skills/local-preview/tests/test_esde_preview.py

esde: ## ES-DE theme preview -> PREVIEW_OUT/system.png, gamelist.png (preview-esde.sh)
	$(Q)$(HERE)/preview-esde.sh "$(THEME)" "$(PREVIEW_OUT)" --view $(VIEW) \
	  $(if $(strip $(KEYS)),--keys "$(KEYS)") $(if $(strip $(FIXTURE)),--fixture "$(FIXTURE)")

esde-system: ## Only the system carousel (VIEW=system)
	$(Q)$(MAKE) --no-print-directory esde VIEW=system

esde-gamelist: ## Only the gamelist view (VIEW=gamelist, KEYS=)
	$(Q)$(MAKE) --no-print-directory esde VIEW=gamelist
