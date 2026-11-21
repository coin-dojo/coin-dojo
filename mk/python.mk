# mk/python.mk - run a folder's python tests the repo's way (ticket #90).
#
# Tests here are plain `python3 <file>` scripts (pytest is not installed). A test file passes when
# it exits 0 and prints no line starting with `FAIL` or `RESULT: FAIL`; a `SKIP ...` line is counted:
# the file's line says `(N skipped: ...)`, the summary `RESULT: PASS (N skipped)`, and a file that prints SKIP
# with a bare `RESULT: PASS` gets a WARN. Every file is run from the repo root, like the SKILL.md/README commands.
#
#   PY_TESTS        test files to run (default: $(HERE)/tests/test_*.py); set before or after include
#   PY_TEST_PYTHON  interpreter for them (default: $(PYTHON) = python3)
#   PY_PIL          python with Pillow: $(BADGE_VENV)/bin/python when that venv exists, else python3
#                   (the badges/controls art tools need Pillow; skills/badges/SKILL.md step 1)
ifndef HK_PYTHON_MK
HK_PYTHON_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk

BADGE_VENV     ?= $(HOME)/.venvs/badges
PY_PIL         := $(firstword $(wildcard $(BADGE_VENV)/bin/python) $(PYTHON))
PY_TESTS       ?= $(sort $(wildcard $(HERE)/tests/test_*.py))
PY_TEST_PYTHON ?= $(PYTHON)

.PHONY: py-test py-list
py-test: ## Run this folder's python tests (plain python3; FAIL on exit != 0 or a FAIL line)
	$(Q)cd "$(REPO_ROOT)" || exit 1; fails=0; skipped=0; \
	for t in $(patsubst $(REPO_ROOT)/%,%,$(PY_TESTS)); do \
	  out=$$($(PY_TEST_PYTHON) "$$t" 2>&1); rc=$$?; \
	  if [ "$(V)" = 1 ]; then printf '%s\n' "$$out"; fi; \
	  if [ $$rc -ne 0 ] || printf '%s\n' "$$out" | grep -qE '^(FAIL|RESULT: FAIL)'; then \
	    [ "$(V)" = 1 ] || printf '%s\n' "$$out"; \
	    echo "RESULT: FAIL $$t (exit $$rc)"; fails=$$((fails + 1)); \
	  else \
	    n=$$(printf '%s\n' "$$out" | grep -c '^SKIP'); skipped=$$((skipped + n)); \
	    first=$$(printf '%s\n' "$$out" | grep -m1 '^SKIP'); \
	    echo "RESULT: PASS $$t$${first:+ ($$n skipped: $$first)}"; \
	    if [ $$n -gt 0 ] && printf '%s\n' "$$out" | grep -E '^RESULT: PASS' | grep -qv 'skipped'; then \
	      echo "WARN $$t prints SKIP and RESULT: PASS without a skip count (print 'RESULT: PASS (N skipped)')"; fi; \
	  fi; \
	done; \
	if [ $$fails -ne 0 ]; then echo "RESULT: FAIL $(HELP_NAME): $$fails test file(s) failed"; exit 1; fi; \
	if [ $$skipped -gt 0 ]; then echo "RESULT: PASS ($$skipped skipped) $(HELP_NAME)"; else echo "RESULT: PASS $(HELP_NAME)"; fi

py-list: ## Print the test files `make test` runs here (paths from the repo root)
	@for t in $(patsubst $(REPO_ROOT)/%,%,$(PY_TESTS)); do echo "$$t"; done

endif
