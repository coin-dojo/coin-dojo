# mk/edition.mk - personal vs community file sets (ticket #90; AGENTS.md "Two repos").
#
#   personal   = every git-tracked file, personal/** included (the maintainer's private art overlay).
#   community  = the same minus personal/**; nothing copyrighted. It must pass the
#                ship/no-artifacts gate (tests/test_ship_no_artifacts.py).
#
#   EDITION_EXCLUDE         top-level paths left out of EDITION (community: personal/)
#   EDITION_RSYNC_EXCLUDES  the same as rsync --exclude flags, for copy/deploy steps
#   EDITION_OVERLAYS        overlay trees laid over the repo files on deploy (personal: personal/themes)
ifndef HK_EDITION_MK
HK_EDITION_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk

EDITION_EXCLUDE_personal   :=
EDITION_EXCLUDE_community  := personal/
EDITION_OVERLAYS_personal  := personal/themes
EDITION_OVERLAYS_community :=

EDITION_EXCLUDE        := $(EDITION_EXCLUDE_$(EDITION))
EDITION_OVERLAYS       := $(EDITION_OVERLAYS_$(EDITION))
EDITION_RSYNC_EXCLUDES := $(foreach p,$(EDITION_EXCLUDE),--exclude=/$(p))
HK_EDITION_RE          := $(subst $(space),|,$(strip $(EDITION_EXCLUDE)))
HK_SHIP_TEST           := tests/test_ship_no_artifacts.py
HK_EDITION_LS           = git -C "$(REPO_ROOT)" ls-files | awk -v re='$(HK_EDITION_RE)' 're == "" || $$0 !~ ("^(" re ")")'

.PHONY: edition-files edition-check
edition-files: ## List the git-tracked files of EDITION (community = without personal/**)
	@$(HK_EDITION_LS)

edition-check: ## Check EDITION: community has no personal/** files, and the ship/no-artifacts gate passes
	$(Q)n=$$($(HK_EDITION_LS) | grep -c '^personal/'); \
	if [ "$(EDITION)" = community ] && [ "$$n" -ne 0 ]; then \
	  echo "RESULT: FAIL edition-check: community edition lists $$n personal/ file(s)" >&2; exit 1; fi; \
	echo "edition-check: EDITION=$(EDITION), $$n personal/ file(s) in the set"
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) $(HK_SHIP_TEST) >/dev/null \
	  || { echo "RESULT: FAIL edition-check: $(HK_SHIP_TEST) failed (run it for details)" >&2; exit 1; }
	@echo "RESULT: PASS edition-check ($(EDITION))"

endif
