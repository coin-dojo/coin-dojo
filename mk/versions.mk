# mk/versions.mk - versions.lock: every pinned third-party component in one generated list (ticket #278).
#
#   make versions                                   regenerate versions.lock from the pin sources (tools/versions.py)
#   make versions-show                              the list on screen, with file:line of every pin
#   make bump C=<component> V=<version> [SHA=...] [COMMIT=...] [URL=...]
#                                                   change a pin in every source that names it, regenerate the lock and
#                                                   add the CHANGELOG `### Versions` line (skills/versions/SKILL.md)
# V here is the new version (mk/flags.mk skips its 0/1 check for `bump`). SHA: one sha256, or tag=value,tag=value
# for a component with several files (versions-show names the tags). URL: the new download URL as a whole, needed where
# the version cannot be derived from it (raspios-lite-arm64: the folder date and the file date can differ).
ifndef HK_VERSIONS_MK
HK_VERSIONS_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk

C      ?=
SHA    ?=
COMMIT ?=
URL    ?=

.PHONY: versions versions-show bump
versions: ## regenerate versions.lock from the pin sources (every pinned third-party component, #278)
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) tools/versions.py generate

versions-show: ## every pinned component with the file:line of each pin
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) tools/versions.py show

bump: ## C=<component> V=<version> [SHA=] [COMMIT=] [URL=]: change a pin everywhere + versions.lock + the CHANGELOG line
	$(Q)$(call need,C,the component, e.g. C=retroarch (make versions-show lists them))
	$(Q)$(call need,V,the new version, e.g. V=v1.22.3)
	$(Q)cd "$(REPO_ROOT)" && $(PYTHON) tools/versions.py bump "$(C)" "$(V)" --sha "$(SHA)" --commit "$(COMMIT)" --url "$(URL)"

endif
