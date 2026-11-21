# mk/help.mk - self-documenting `make help` (ticket #90; #142: forwarded targets, quoted flags, renamed names).
#
# Document a target with a trailing `## ` comment on its rule line:
#     deploy: ## Dry-run the Ansible deploy (APPLY=1 writes)
# Document a Makefile's own variable with a `#? NAME text` line (NAME may hold a quoted value with spaces):
#     #? W=DIR  scratch workspace outside git (required)
#     #? CLOSE="n m",TICKET=none  merge: extra tickets to close
# Forwarded targets: a Makefile that sets HK_FWD_DIRS := <dir> and HK_FWD_<dir> := t1 t2 gets those targets listed
# once, with the `##` text of <dir>/Makefile (one source). `help` is the default goal. Targets are listed in file order, the Makefile first, then the mk/*.mk it
# includes; a file included several times is read once.
ifndef HK_HELP_MK
HK_HELP_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk

.DEFAULT_GOAL := help
HELP_NAME ?= $(if $(filter $(REPO_ROOT),$(HERE)),.,$(patsubst $(REPO_ROOT)/%,%,$(HERE)))
HK_FWD_DIRS ?=
# awk prelude: read each file once (MAKEFILE_LIST repeats a file every time it is included).
HK_HELP_ONCE := FNR == 1 { dup = seenf[FILENAME]++ } dup { next }
HK_HELP_TGT  := /^[A-Za-z0-9_.%-]+:([^=]|$$)/ && / \#\# /

.PHONY: help
help: ## List targets and flags
	@echo "coin-dojo make: $(HELP_NAME)"
	@echo ""
	@echo "Targets:"
	@awk -F':.*## ' '$(HK_HELP_ONCE) $(HK_HELP_TGT) && !seen[$$1]++ { printf "  %-18s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@$(foreach d,$(HK_FWD_DIRS),awk -F':.*## ' -v want=" $(strip $(HK_FWD_$(d))) " -v dir="$(d)" \
	  '$(HK_HELP_TGT) && index(want, " " $$1 " ") && !seen[$$1]++ { if (!h++) printf "\nForwarded to %s/:\n", dir; printf "  %-18s %s\n", $$1, $$2 }' \
	  "$(REPO_ROOT)/$(d)/Makefile";) true
	@echo ""
	@echo "Flags (current value):"
	@printf '  %-18s %s\n' "ARCH=$(ARCH)" "CPU arch: $(HK_ARCHES) (x86_64 planned)"
	@printf '  %-18s %s\n' "EDITION=$(EDITION)" "personal (incl. personal/**) | community (no personal/**)"
	@printf '  %-18s %s\n' "APPLY=$(APPLY)" "1 = write; cabinet/host targets dry-run otherwise"
	@printf '  %-18s %s\n' "V=$(V)" "1 = echo commands, full test output"
	@awk '$(HK_HELP_ONCE) /^#\? / { s = $$0; sub(/^#\? /, "", s); if (seen[s]++ || !match(s, /^([^ "]|"[^"]*")+/)) next; \
	  n = substr(s, 1, RLENGTH); d = substr(s, RLENGTH + 1); sub(/^ +/, "", d); printf "  %-18s %s\n", n, d }' $(MAKEFILE_LIST)

endif
