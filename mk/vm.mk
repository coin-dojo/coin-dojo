# mk/vm.mk - coin-dojo-vm, the throwaway Debian 13 test VM on the Mac (ticket #82; vm/README.md).
#
# lima (Apple vz) runs vm/coin-dojo-test.yaml; ansible provisions it (vm.yml, LIMIT=coin-dojo-vm);
# the cabinet stack (cage -> ES-DE -> RetroArch) then runs headless on llvmpipe. It is a VM, never the
# cabinet: these targets only talk to lima on this Mac, and Ansible's VM runs pass vm-guard first.
# The maintainer's CPU rule: every heavy step (start, provision, smoke) waits for cpu-gate (< 80% CPU) first, and
# the VM is left STOPPED when you are done (make vm-down).
#
#   VM_NAME       lima instance and inventory host (coin-dojo-vm)
#   VM_TEMPLATE   the lima template (vm/coin-dojo-test.yaml)
#   VM_OUT        where vm-smoke copies the screenshots (default $(HK_CACHE)/vm/smoke)
#   VM_CPU_GATE   the CPU gate command (default tools/cpu-gate.sh, give up = fail; empty = skip)
#   VM_GATE_TRIES waits before the gate gives up and fails (default 0 = wait forever)
#   LIMACTL       the limactl command (tests point it at a stub)
ifndef HK_VM_MK
HK_VM_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk
include $(dir $(lastword $(MAKEFILE_LIST)))config.mk

VM_NAME      ?= $(HK_VM_HOST)
VM_TEMPLATE  ?= $(REPO_ROOT)/vm/coin-dojo-test.yaml
VM_OUT       ?= $(HK_CACHE)/vm/smoke
VM_CPU_GATE  ?= CPU_GATE_ON_TIMEOUT=fail CPU_GATE_TRIES=$(VM_GATE_TRIES) sh $(REPO_ROOT)/tools/cpu-gate.sh
VM_GATE_TRIES ?= 0
VM_VNC_PORT  := 5924
LIMACTL      ?= limactl
VM_SHELL      = $(LIMACTL) shell --workdir / $(VM_NAME)
# The VM's login follows the cabinet's (hk_cabinet_user / hk_cabinet_uid / hk_cabinet_home, #149): the template holds
# neutral values, vm-up passes the configured ones to lima on creation. Lazy: `make help` never runs the loader.
VM_CFG        = $(CONFIG_PY) get --limit $(HK_VM_HOST)
VM_USER_SET   = ".user.name = \"$$($(VM_CFG) hk_cabinet_user)\" | .user.uid = $$($(VM_CFG) hk_cabinet_uid) | .user.home = \"$$($(VM_CFG) hk_cabinet_home)\""
# 0 = no such instance; else its status (Running / Stopped / ...).
VM_STATUS     = $$($(LIMACTL) list --format '{{.Status}}' $(VM_NAME) 2>/dev/null || true)
VM_GATE       = $(if $(strip $(VM_CPU_GATE)),$(VM_CPU_GATE) || exit 1;,)
VM_NEED_UP    = st=$(VM_STATUS); [ "$$st" = Running ] || { echo "$(VM_NAME) is not running ($${st:-not created}): make vm-up" >&2; exit 1; }

#? VM_OUT=dir  vm-smoke: where the screenshots go (default ~/.cache/coin-dojo/vm/smoke)

.PHONY: vm-up vm-down vm-ssh vm-status vm-provision vm-smoke vm-watch vm-watch-stop vm-destroy

vm-up: ## Create (first time) or start coin-dojo-vm, the test VM (lima/vz, 4 CPU / 4 GiB; waits for cpu-gate)
	$(Q)st=$(VM_STATUS); \
	if [ "$$st" = Running ]; then echo "vm-up: $(VM_NAME) already running"; exit 0; fi; \
	$(VM_GATE) t0=$$(date +%s); \
	if [ -z "$$st" ]; then $(LIMACTL) start --tty=false --name=$(VM_NAME) --set $(VM_USER_SET) "$(VM_TEMPLATE)"; \
	else $(LIMACTL) start --tty=false $(VM_NAME); fi || exit 1; \
	echo "vm-up: $(VM_NAME) running after $$(( $$(date +%s) - t0 )) s (make vm-down when done)"

vm-down: ## Stop coin-dojo-vm (the screen session first); always leave it stopped when done
	$(Q)st=$(VM_STATUS); \
	if [ "$$st" != Running ]; then echo "vm-down: $(VM_NAME) is $${st:-not created}, nothing to stop"; exit 0; fi; \
	$(VM_SHELL) hk-session stop >/dev/null 2>&1; \
	$(LIMACTL) stop $(VM_NAME) && echo "vm-down: $(VM_NAME) stopped"

vm-ssh: ## Shell in coin-dojo-vm as coindojo (limactl shell)
	$(Q)$(VM_NEED_UP); $(LIMACTL) shell $(VM_NAME)

vm-status: ## lima status of coin-dojo-vm; when running also load, memory and the screen session
	$(Q)$(LIMACTL) list $(VM_NAME) 2>/dev/null || echo "vm-status: $(VM_NAME) not created (make vm-up)"
	$(Q)if [ "$(VM_STATUS)" = Running ]; then \
	  $(VM_SHELL) sh -c 'uptime; free -m | sed -n 1,3p; command -v hk-session >/dev/null && hk-session status || echo "not provisioned (make vm-provision)"'; fi

vm-provision: ## Ansible: vm.yml on coin-dojo-vm (vm_base + cabinet roles), applied; vm-guard runs first
	$(Q)$(VM_NEED_UP); $(VM_GATE) t0=$$(date +%s); \
	$(MAKE) -C $(REPO_ROOT)/ansible deploy LIMIT=$(VM_NAME) APPLY=1 BECOME=0 || exit 1; \
	echo "vm-provision: done in $$(( $$(date +%s) - t0 )) s"

vm-smoke: ## Smoke test in the VM: cage+ES-DE, then RetroArch+core+test ROM; alive at 10 s + screenshots
	$(Q)$(VM_NEED_UP); $(VM_GATE) rc=0; \
	$(VM_SHELL) /usr/local/lib/coin-dojo-vm/smoke.sh /tmp/coin-dojo-smoke || rc=$$?; \
	rm -rf "$(VM_OUT)"; mkdir -p "$(dir $(VM_OUT))"; \
	$(LIMACTL) copy -r $(VM_NAME):/tmp/coin-dojo-smoke "$(VM_OUT)" >/dev/null 2>&1 \
	  && echo "vm-smoke: screenshots in $(VM_OUT)" || echo "vm-smoke: could not copy the screenshots" >&2; \
	exit $$rc

vm-watch: ## Watch the VM screen on this Mac: ES-DE session + wayvnc, opens vnc://127.0.0.1:5924 (Screen Sharing)
	$(Q)$(VM_NEED_UP); \
	$(VM_SHELL) sh -c 'hk-session start && hk-session vnc' || exit 1; \
	echo "vm-watch: vnc://127.0.0.1:$(VM_VNC_PORT) (Mac loopback only, no password). Stop: make vm-watch-stop"; \
	open "vnc://127.0.0.1:$(VM_VNC_PORT)"

vm-watch-stop: ## Stop the VM's screen session and wayvnc (the VM keeps running; make vm-down stops it)
	$(Q)$(VM_NEED_UP); $(VM_SHELL) hk-session stop

vm-destroy: ## Delete coin-dojo-vm and its disk (dry run unless APPLY=1); vm-up recreates it from the template
ifeq ($(HK_APPLY),)
	@echo "DRY RUN vm-destroy: would run $(LIMACTL) delete --force $(VM_NAME) (status: $(VM_STATUS)). APPLY=1 to delete."
else
	$(Q)$(LIMACTL) delete --force $(VM_NAME) && echo "vm-destroy: $(VM_NAME) deleted"
endif

endif
