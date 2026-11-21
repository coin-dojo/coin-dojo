# mk/cabinet.mk - the cabinet's address and two shared cabinet rules (ticket #90).
#
# The real deploy path is Ansible (ansible, `make deploy`). These rules are for the few
# targets that copy a file or restart ES-DE by hand, as the SKILL.md steps do. Every target that
# writes to the cabinet is a dry run unless APPLY=1, and depends on cabinet-guard when it writes:
#     install: $(if $(HK_APPLY),cabinet-guard)
#
#   CABINET_HOST/USER/KEY  from config: hk_cabinet_host / hk_ssh_user / hk_ssh_key (key auth, no password)
#   CABINET_GUARD_CMD      the remote pgrep line, from hk_game_procs / hk_game_patterns
#   CABINET_SSH            the ssh command; override it to point the rules at a stub in tests
#   CABINET_SCP            the scp command
#   CABINET_LOCK           prefix of every run that talks to the cabinet for a while (bin/deploy, smoke-launch): the
#                          cabinet lane of tools/cilock.py (#134), one run at a time, never queued behind heavy
#                          test runs; a no-op under CILOCK_HELD=1 / CI_NO_LOCK=1 (premerge, nested runs)
#   ES_RESTART_WAIT        seconds esde-restart waits before checking ES-DE is back (default 25)
ifndef HK_CABINET_MK
HK_CABINET_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk
include $(dir $(lastword $(MAKEFILE_LIST)))config.mk

# The cabinet's address, user and key come from the config (tools/config.py; #118): config/local.yml or
# personal/config/local.yml. Lazy (=), so `make help` and friends never run the loader.
CABINET_HOST    ?= $(shell $(CONFIG_PY) get hk_cabinet_host 2>/dev/null)
CABINET_USER    ?= $(shell $(CONFIG_PY) get hk_ssh_user 2>/dev/null)
CABINET_KEY     ?= $(shell $(CONFIG_PY) get hk_ssh_key 2>/dev/null)
CABINET_GUARD_CMD ?= $(shell $(CONFIG_PY) guard-cmd 2>/dev/null)
CABINET          = $(CABINET_USER)@$(CABINET_HOST)
CABINET_SSH     ?= ssh -i $(CABINET_KEY) -o BatchMode=yes -o ConnectTimeout=10 $(CABINET)
CABINET_SCP     ?= scp -i $(CABINET_KEY) -o BatchMode=yes -o ConnectTimeout=10
ES_RESTART_WAIT ?= 25
CABINET_LOCK    ?= $(REPO_ROOT)/tools/cilock.py run cabinet --

.PHONY: cabinet-guard esde-restart
cabinet-guard: $(HK_CFG_GATE) ## Stop unless the cabinet is reachable and no game (retroarch, retroarch-1.22, flycast) is running
	$(Q)busy=$$($(CABINET_SSH) '$(CABINET_GUARD_CMD)') \
	  || { echo "cabinet-guard: cannot reach $(CABINET)" >&2; exit 1; }; \
	if [ -n "$$busy" ]; then \
	  echo "cabinet-guard: a game is running on $(CABINET) (pid $$busy); quit it first (Select+Start)" >&2; exit 1; \
	fi; \
	echo "cabinet-guard: OK, no game running on $(CABINET)"

# ES-DE runs as AppRun (an AppImage) inside `exec cage`; when it dies the tty1 autologin starts the
# session again. A launch loop can trip the getty@tty1 start limit: runbooks/cabinet-recovery.md 3c.
esde-restart: $(HK_CFG_GATE) $(if $(HK_APPLY),cabinet-guard) ## Restart ES-DE on the cabinet (pkill -9 -x AppRun, check AppRun + getty); APPLY=1
ifeq ($(HK_APPLY),)
	@echo "DRY RUN esde-restart: would run on $(CABINET): pkill -9 -x AppRun, wait $(ES_RESTART_WAIT) s, check pgrep -f AppRun and getty@tty1. APPLY=1 to run."
else
	$(Q)$(CABINET_SSH) 'pkill -9 -x AppRun; true'
	$(Q)sleep $(ES_RESTART_WAIT)
	$(Q)if $(CABINET_SSH) 'pgrep -f AppRun' >/dev/null; then echo "esde-restart: OK, ES-DE (AppRun) is back"; else \
	  echo "esde-restart: ES-DE is not back; getty@tty1 is:" >&2; \
	  $(CABINET_SSH) 'systemctl is-active getty@tty1; true' >&2; \
	  echo "esde-restart: see runbooks/cabinet-recovery.md section 3c (getty start limit)" >&2; exit 1; fi
endif

# --- read-only cabinet helpers (#143): never write to the cabinet ---------------------------------------------------
CABINET_SSH_TTY ?= ssh -t -i $(CABINET_KEY) -o ConnectTimeout=10 $(CABINET)
HK_SHOTS        ?= $(HK_CACHE)/shots
ESDE_HOME_CFG    = $(shell $(CONFIG_PY) get hk_esde_home 2>/dev/null)
THEME_CFG        = $(shell $(CONFIG_PY) get hk_theme_name 2>/dev/null)

.PHONY: cabinet-status cabinet-ssh cabinet-shot
cabinet-status: $(HK_CFG_GATE) ## Read-only health in one ssh call: host, uptime, ES-DE, running game, page size, df, zram, deployed REV vs this checkout
	$(Q)CABINET_SSH="$(CABINET_SSH)" CABINET="$(CABINET)" GUARD_CMD='$(CABINET_GUARD_CMD)' ESDE_HOME="$(ESDE_HOME_CFG)" \
	  THEME="$(THEME_CFG)" LOCAL_REV="$(COIN_DOJO_VERSION)" bash $(REPO_ROOT)/tools/cabinet-status.sh

.PHONY: net-check
NET_CHECK_HOST_CFG = $(shell $(CONFIG_PY) get hk_net_check_host 2>/dev/null)
net-check: $(HK_CFG_GATE) ## Read-only 60 s ping from the cabinet to a public host + its gateway: avg/max/mdev/loss (#213; NET_LOCAL=1 = this machine, NET_SECONDS=n)
	$(Q)NET_CHECK_SSH="$(if $(NET_LOCAL),,$(CABINET_SSH))" CABINET="$(CABINET)" NET_CHECK_HOST="$(NET_CHECK_HOST_CFG)" \
	  NET_CHECK_SECONDS="$(or $(NET_SECONDS),60)" bash $(REPO_ROOT)/tools/net-check.sh

cabinet-ssh: $(HK_CFG_GATE) ## Interactive shell on the cabinet (key auth; CABINET_SSH_TTY)
	$(Q)$(CABINET_SSH_TTY)

cabinet-shot: $(HK_CFG_GATE) ## Full-size screenshot of the live screen (grim to stdout, nothing written on the cabinet) -> HK_CACHE/shots/<stamp>.png
	$(Q)mkdir -p "$(HK_SHOTS)" && f="$(HK_SHOTS)/cabinet-$$(date +%Y%m%d-%H%M%S).png"; \
	if $(CABINET_SSH) 'XDG_RUNTIME_DIR=/run/user/$$(id -u) WAYLAND_DISPLAY=$${WAYLAND_DISPLAY:-wayland-0} grim -t png -' > "$$f" && [ -s "$$f" ]; then \
	  echo "cabinet-shot: $$f"; else rm -f "$$f"; echo "cabinet-shot: FAILED (ES-DE down, or no wayland session on $(CABINET))" >&2; exit 1; fi

endif
