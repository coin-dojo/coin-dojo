# mk/lint.mk - `make lint` and `make secrets` (ticket #124; pattern from the maintainer's other repo's lint and security jobs).
#
# Every tool runs in the pinned Ansible image (ansible/Dockerfile + requirements.txt; ansible/bin/ans builds it
# on first use or after a pin change): shellcheck, yamllint, ansible-lint, ruff, gitleaks. Nothing is installed on
# the Mac. Configs live at the repo root: .shellcheckrc, .yamllint, ruff.toml, .gitleaks.toml (+ ansible/.ansible-lint).
# File lists come from tools/lintfiles.py (tracked files only; shell and python scripts without an extension
# are found by their shebang). Every check runs even after one fails; the last line is the verdict.
#   LINT_ONLY=shell|yaml|python|ansible  run only that check (default: all)
#   GITLEAKS_ALL_REFS=1  secrets: scan every ref of the repo, not just HEAD's history (CI does this)
ifndef HK_LINT_MK
HK_LINT_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk

LINT_ONLY ?=
HK_VALIDATED += $(if $(LINT_ONLY),$(call hk_valid,LINT_ONLY,shell yaml python ansible))
LINT_ANS := $(REPO_ROOT)/ansible/bin/ans
# Colima shares only $$HOME. A git worktree's .git file points at the main checkout's .git, outside this repo:
# mount it (read-only, same path) through ANS_WORKSPACE so `git clone` works in the container.
LINT_GITDIR := $(shell cd "$(REPO_ROOT)" && d=$$(git rev-parse --git-common-dir 2>/dev/null) && cd "$$d" && pwd -P)
LINT_WS     := $(if $(filter $(REPO_ROOT)/%,$(LINT_GITDIR)/),,$(LINT_GITDIR))

.PHONY: lint secrets lint-shell lint-yaml lint-python lint-ansible

lint: ## shellcheck, yamllint, ruff, ansible-lint (+ site.yml syntax, through LINT_ANS like the rest, #133) in the pinned image; LINT_ONLY=shell|yaml|python|ansible
	$(Q)cd "$(REPO_ROOT)" && fails=; \
	for k in shell yaml python ansible; do \
	  if [ -n "$(LINT_ONLY)" ] && [ "$(LINT_ONLY)" != $$k ]; then continue; fi; \
	  echo "== lint $$k"; $(MAKE) lint-$$k || fails="$$fails $$k"; \
	done; \
	if [ -n "$$fails" ]; then echo "RESULT: FAIL lint:$$fails"; exit 1; fi; echo "RESULT: PASS lint"

lint-shell:
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) tools/lintfiles.py sh | xargs -0 $(LINT_ANS) shellcheck -x

lint-yaml:
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) tools/lintfiles.py yaml | xargs -0 $(LINT_ANS) yamllint -s -c .yamllint

lint-python:
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) tools/lintfiles.py py | xargs -0 $(LINT_ANS) ruff check --no-cache --config ruff.toml

lint-ansible:
	$(Q)cd "$(REPO_ROOT)/ansible" && ANSIBLE_CONFIG=ansible.cfg $(LINT_ANS) ansible-lint --nocolor -q .
	$(Q)cd "$(REPO_ROOT)/ansible" && $(LINT_ANS) ansible-playbook site.yml --syntax-check $(ANSIBLE_EXTRA_VARS) \
	  && echo "RESULT: PASS ansible-syntax" || { echo "RESULT: FAIL ansible-syntax"; exit 1; }

secrets: ## gitleaks on a bare clone made in the pinned image: history (HEAD; GITLEAKS_ALL_REFS=1 = all refs) + working copy
	$(Q)cd "$(REPO_ROOT)" && ANS_WORKSPACE="$(LINT_WS)" GITLEAKS_ALL_REFS="$(GITLEAKS_ALL_REFS)" \
	  $(LINT_ANS) sh tools/secrets-scan.sh "$(REPO_ROOT)" && echo "RESULT: PASS secrets" || { echo "RESULT: FAIL secrets"; exit 1; }
endif
