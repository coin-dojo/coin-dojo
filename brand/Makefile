# brand - the coin-dojo style kit (brand/BRAND.md). Tests run with the Pillow venv python when it exists
# (~/.venvs/badges, skills/badges/SKILL.md step 1), so the byte-identical generator checks run instead of SKIP;
# the RESULT line counts any skip.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk
PY_TEST_PYTHON := $(PY_PIL)

.PHONY: test check specimen logo

test: py-test ## Style kit tests: tokens, contrast, theme agreement, generators byte-identical, no-literals lint

check: ## Contrast table of every token pair (RESULT: PASS/FAIL)
	$(Q)$(PYTHON) $(HERE)/brand.py check

specimen: ## Re-render brand/specimen.png from the tokens (Pillow venv); look at it afterwards
	$(Q)$(PY_PIL) $(HERE)/specimen.py

logo: ## Re-render the Coin Dojo logo (brand/logo*.png) from the tokens (Pillow venv); look at it afterwards
	$(Q)$(PY_PIL) $(HERE)/gen_logo.py
