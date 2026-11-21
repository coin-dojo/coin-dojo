# mk/ci.mk - CI targets: pick tests by changed path, one file, one area, `make ci` (ticket #123, T10b of #103).
#
# ci/rules.tsv (path -> areas) + ci/areas.tsv (area -> test files + lane) are read by tools/ci_select.py. A selector
# error runs everything (--failopen). Tests run through tools/runtests.py, one RESULT line per file, under the host-wide
# lock (tools/cilock.py): light lane for one file / small selections, heavy lane when an area is heavy or > 10 files.
#
#   BASE   ?= origin/main   the diff base of test-changed / test-list / ci
#   F=     test-file: the test file;   A= test-area: area name(s), comma separated
#   FULL=1 ci: everything (test + community edition + config + changelog)
ifndef HK_CI_MK
HK_CI_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk

BASE      ?= origin/main
HK_SELECT := $(PYTHON) $(REPO_ROOT)/tools/ci_select.py
HK_LANE_N := 10

# $(call hk_run_tests,SELECT-ARGS): list the selected test files, pick the lane, run them.
define hk_run_tests
cd "$(REPO_ROOT)" || exit 1; \
files=$$($(HK_SELECT) $(1) --failopen --format tests) || { echo "RESULT: FAIL $(2): selector"; exit 1; }; \
lane=$$($(HK_SELECT) $(1) --failopen --format lane); \
n=$$(printf '%s\n' "$$files" | grep -c .); \
[ "$$n" -gt $(HK_LANE_N) ] && lane=heavy; \
echo "$(2): $$n test file(s), $$lane lane"; \
if [ "$$n" -eq 0 ]; then echo "RESULT: PASS $(2) (no test files selected)"; exit 0; fi; \
$(REPO_ROOT)/tools/cilock.py run $$lane -- $(PYTHON) $(REPO_ROOT)/tools/runtests.py --label "$(2)" $$files
endef

.PHONY: test-changed test-file test-area test-list ci
test-changed: ## BASE=origin/main: only the tests the changed paths need (ci/rules.tsv), plus the always list
	$(Q)$(call hk_run_tests,--base $(BASE),make test-changed)

test-file: ## F=path/test_x.py: one test file (light lane, seconds)
	$(Q)$(call need,F,test file, e.g. F=tools/tests/test_select.py)
	$(Q)cd "$(REPO_ROOT)" && $(REPO_ROOT)/tools/cilock.py run light -- $(PYTHON) $(REPO_ROOT)/tools/runtests.py --label "make test-file" $(F)

test-area: ## A=ansible|collections|controls|theme|brand|preview|settings|build|vm|smoke|tools|docs|always (comma list ok)
	$(Q)$(call need,A,area name from ci/areas.tsv, e.g. A=tools)
	$(Q)$(call hk_run_tests,--areas $(A),make test-area $(A))

test-list: ## BASE=origin/main: show the rule that matched each changed path, the areas, lane and test files
	$(Q)$(HK_SELECT) --base $(BASE) --failopen --explain

ci: ## [BASE=origin/main] [FULL=1]: lint + security + the tests a change needs (FULL: all tests + community edition + config + changelog)
	$(Q)$(PYTHON) $(REPO_ROOT)/tools/ci.py --base $(BASE) $(if $(filter 1,$(FULL)),--full)

endif
