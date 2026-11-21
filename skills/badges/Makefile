# skills/badges - regenerate the theme's original art (ticket #90). Commands from SKILL.md; all of
# them need Pillow, so they run with PY_PIL (~/.venvs/badges/bin/python when it exists; create it
# with `make venv`). Same inputs = identical bytes. Writes into the repo theme only.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/../..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk

#? THEME=DIR  theme to write into (default themes/coin-dojo)
#? OUT=F      badge: the PNG to write
#? TEXT=T     badge: the text drawn on it
THEME ?= $(REPO_ROOT)/themes/coin-dojo
ART   := $(THEME)/_inc/art
OUT   ?=
TEXT  ?=
PY_TEST_PYTHON := $(PY_PIL)

.PHONY: test venv badge chips help-icons scrim deck panels frames art

test: py-test ## badge, chips and palette-art tests (Pillow venv; they SKIP without Pillow)

venv: ## One-time: create the Pillow venv BADGE_VENV (~/.venvs/badges) (SKILL step 1)
	$(Q)python3 -m venv "$(BADGE_VENV)" && "$(BADGE_VENV)/bin/pip" install pillow

badge: ## One carousel badge: OUT=_inc/systems/<name>.png TEXT="MUST PLAY" (SKILL step 2)
	$(call need,OUT,the badge PNG path)
	$(call need,TEXT,the badge text)
	$(Q)$(PY_PIL) $(HERE)/badge.py "$(OUT)" "$(TEXT)"

chips: ## Category chips + genre pictograms for every badge (gen_chips.py THEME)
	$(Q)$(PY_PIL) $(HERE)/gen_chips.py "$(THEME)"

help-icons: ## Help-bar button icons + hotkeys strip (gen_help.py THEME)
	$(Q)$(PY_PIL) $(HERE)/gen_help.py "$(THEME)"

scrim: ## System/gamelist scrims (gen_scrim.py ART)
	$(Q)$(PY_PIL) $(HERE)/gen_scrim.py "$(ART)"

deck: ## Footer deck.png (gen_deck.py ART/deck.png)
	$(Q)$(PY_PIL) $(HERE)/gen_deck.py "$(ART)/deck.png"

panels: ## panel.png / panel-red.png (gen_panels.py ART)
	$(Q)$(PY_PIL) $(HERE)/gen_panels.py "$(ART)"

frames: ## Gamelist panel frames (gen_frames.py ART/frames)
	$(Q)$(PY_PIL) $(HERE)/gen_frames.py "$(ART)/frames"

art: chips help-icons scrim deck panels frames ## Regenerate all generated art, then check the theme
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) themes/tests/test_theme_xml.py | tail -n 1
