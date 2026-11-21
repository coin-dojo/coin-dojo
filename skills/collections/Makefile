# skills/collections - ES-DE collections, nested main screen, names, dupes, metadata (ticket #90).
# The commands are the ones in SKILL.md; collgen.py plans by default and APPLY=1 adds --apply
# (writes under W only). The only cabinet write is `install-cfg` (dry run unless APPLY=1).
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/../..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk
include $(REPO_ROOT)/mk/cabinet.mk

#? W=DIR      scratch workspace outside git, e.g. W=~/coin-dojo-collections (required)
#? PICKS=F    Cabinet Picks page (default W/cabinet-picks.html)
#? DAT=F      libretro FBNeo dat (default W/libretro.dat)
#? ARGS=...   extra collgen.py flags, e.g. ARGS="--series skills/collections/series.tsv"
W     ?=
PICKS ?= $(W)/cabinet-picks.html
DAT   ?= $(W)/libretro.dat
ZIPS  ?= $(W)/fbneo_zips.txt
ARGS  ?=
COLLGEN := $(PYTHON) $(HERE)/collgen.py

.PHONY: test gamelists fbneo-zips fbneo-gamelist build nested names dupes metadata install-cfg

test: py-test ## collgen, nested, series, dupes, names, launch, metadata tests

gamelists: ## Copy the cabinet's gamelists to W/gamelists (read-only on the cabinet; SKILL step 1)
	$(call need,W,scratch workspace)
	$(Q)mkdir -p "$(W)/gamelists"
	$(Q)$(CABINET_SCP) -r '$(CABINET):ES-DE/gamelists/*' "$(W)/gamelists/"

fbneo-zips: ## List the cabinet's FBNeo zips into W/fbneo_zips.txt (read-only; SKILL step 2)
	$(call need,W,scratch workspace)
	$(Q)$(CABINET_SSH) ls /data/roms/fbneo > "$(ZIPS)"

fbneo-gamelist: ## Plan the FBNeo gamelist (APPLY=1 writes W/gamelists/fbneo/gamelist.xml; step 3)
	$(call need,W,scratch workspace)
	$(Q)$(COLLGEN) fbneo-gamelist --dat "$(DAT)" --zips "$(ZIPS)" --picks "$(PICKS)" \
	  --out "$(W)/gamelists/fbneo/gamelist.xml" $(APPLY_FLAG) $(ARGS)

build: ## Plan the collections -> W/collections_out (APPLY=1 writes; steps 4-5)
	$(call need,W,scratch workspace)
	$(Q)$(COLLGEN) build --gamelists "$(W)/gamelists" --picks "$(PICKS)" --out "$(W)/collections_out" $(APPLY_FLAG) $(ARGS)

nested: ## Plan the nested main screen -> W/nested_out (APPLY=1 writes; ARGS for --series/--media/...)
	$(call need,W,scratch workspace)
	$(Q)$(COLLGEN) nested --gamelists "$(W)/gamelists" --picks "$(PICKS)" --out "$(W)/nested_out" $(APPLY_FLAG) $(ARGS)

names: ## Plan clean display names in W/gamelists (APPLY=1 writes, with .bak copies)
	$(call need,W,scratch workspace)
	$(Q)$(COLLGEN) names --gamelists "$(W)/gamelists" $(APPLY_FLAG) $(ARGS)

dupes: ## Plan hiding the weaker duplicates (duplicates.tsv) in W/gamelists (APPLY=1 writes)
	$(call need,W,scratch workspace)
	$(Q)$(COLLGEN) dupes --gamelists "$(W)/gamelists" $(APPLY_FLAG) $(ARGS)

metadata: ## Plan filling missing metadata from W/romm.jsonl (APPLY=1 writes; ARGS for --staging/--want)
	$(call need,W,scratch workspace)
	$(Q)$(COLLGEN) metadata --gamelists "$(W)/gamelists" --romm "$(W)/romm.jsonl" --dat "$(DAT)" \
	  --picks "$(PICKS)" --media "$(W)/media" $(APPLY_FLAG) $(ARGS)

install-cfg: $(if $(HK_APPLY),cabinet-guard) ## Copy W/collections_out/custom-*.cfg to the cabinet (dry run unless APPLY=1; step 6)
	$(call need,W,scratch workspace)
ifeq ($(HK_APPLY),)
	@echo "DRY RUN install-cfg: would copy to $(CABINET):ES-DE/collections/:"; ls "$(W)"/collections_out/custom-*.cfg
else
	$(Q)$(CABINET_SCP) "$(W)"/collections_out/custom-*.cfg $(CABINET):ES-DE/collections/
	@echo "install-cfg: copied; ES-DE picks them up on its next start (not restarted here)"
endif
