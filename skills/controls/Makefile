# skills/controls - controls cards + in-game overlays (ticket #90). Commands from SKILL.md steps 1-3,
# all on the laptop (step 1 only reads the cabinet). Steps 4-6 (copy to the cabinet, backup,
# gamelists/overrides --apply, restart) stay manual / Ansible: see SKILL.md.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/../..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk
include $(REPO_ROOT)/mk/cabinet.mk

#? W=DIR      new empty scratch dir: W/gl gamelists, W/out render output (required)
#? ONLY=sys   render: limit overlays to these systems (e.g. ONLY=n64)
W     ?=
ONLY  ?=
FONTS := $(REPO_ROOT)/themes/coin-dojo/_inc/fonts

.PHONY: test gamelists plan render

test: py-test ## controls.py tests (incl. the unsafe-ROM-stem broken case)

gamelists: ## Copy the cabinet's gamelists to W/gl (read-only on the cabinet; step 1)
	$(call need,W,scratch dir)
	$(Q)mkdir -p "$(W)/gl" && $(CABINET_SSH) 'cd ~/ES-DE/gamelists && tar cf - */gamelist.xml' | tar xf - -C "$(W)/gl"

plan: ## Layout per system + games with a card (controls.py plan; step 2)
	$(call need,W,scratch dir)
	$(Q)$(PYTHON) $(HERE)/controls.py plan --gamelists "$(W)/gl"

render: ## Render cards + overlays + overrides -> W/out (Pillow; step 3)
	$(call need,W,scratch dir)
	$(Q)$(PY_PIL) -I $(HERE)/controls.py render --gamelists "$(W)/gl" --fonts "$(FONTS)" --out "$(W)/out" $(if $(strip $(ONLY)),--only $(ONLY))
