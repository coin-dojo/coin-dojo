# mk/smoke.mk - `make smoke-launch`: one game per system, launched through launch.py on the live cage
# session, grim screenshot, contact sheet (ticket #48; tests/smoke/smoke_launch.py).
#
# This RUNS emulators on the target (the cabinet by default), one system at a time, ~15 s each, on top
# of ES-DE. ES-DE restarts TWICE (#104): attract mode (ScreensaverTimer) is set to 0 before the first game and the original value
# put back after the last (also on FAIL / Ctrl-C / ssh drop); see runbooks/smoke-launch.md. It refuses to start while retroarch/flycast runs (the maintainer may be playing)
# or when ES-DE is down. RetroArch runs with config_save_on_exit off and flycast's emu.cfg is
# checked and restored, so nothing on the target is rewritten. Every process is SIGKILLed (its own process group, never by name) after its check.
# On the cabinet it first runs cabinet-guard (mk/cabinet.mk); smoke_remote.sh guards again on the target.
#
#   LIMIT         coin-dojo (default, the cabinet) or coin-dojo-vm (the test VM; make vm-up first: only
#                 systems with a game + core there run, the others SKIP)
#   SYSTEMS       comma separated subset, e.g. SYSTEMS=nes,n64 (default: every system in smoke_games.tsv)
#   SHOT          full (default: kept screenshots at the cabinet's native 1920x1080, CRT scanlines visible) or small (half size)
#   SMOKE_OUT     where the screenshots + contact sheet go (default $(HK_CACHE)/smoke/<stamp>/, HK_CACHE = ~/.cache/coin-dojo)
#   SMOKE_PYTHON_ARGS  extra smoke_launch.py flags (e.g. --no-motion)
ifndef HK_SMOKE_MK
HK_SMOKE_MK := 1
include $(dir $(lastword $(MAKEFILE_LIST)))flags.mk
include $(dir $(lastword $(MAKEFILE_LIST)))config.mk
include $(dir $(lastword $(MAKEFILE_LIST)))cabinet.mk
include $(dir $(lastword $(MAKEFILE_LIST)))vm.mk

SMOKE_OUT         ?= $(HK_CACHE)/smoke
SMOKE_PYTHON_ARGS ?=
SHOT              ?= full
SMOKE_TARGET       = $(if $(filter $(HK_VM_HOST),$(LIMIT)),vm,cabinet)

#? SYSTEMS=a,b  smoke-launch: only these systems (default all 17 in tests/smoke/smoke_games.tsv)
#? LIMIT=coin-dojo-vm  smoke-launch: run in the test VM instead of the cabinet (default coin-dojo)
#? SHOT=full|small  smoke-launch: kept screenshots at native size (default full) or half size
#? SMOKE_OUT=dir  smoke-launch: screenshots + contact sheet root (default ~/.cache/coin-dojo/smoke)

.PHONY: smoke-launch
smoke-launch: $(HK_CFG_GATE) ## RUNS emulators: one game per system via launch.py on the live session, alive at 10 s + grim shot, contact sheet; LIMIT=coin-dojo-vm for the VM
	$(Q)case "$(or $(LIMIT),coin-dojo)" in coin-dojo|$(HK_VM_HOST)) ;; *) echo "smoke-launch: LIMIT must be coin-dojo or $(HK_VM_HOST), not '$(LIMIT)'" >&2; exit 2;; esac
	$(Q)$(if $(filter cabinet,$(SMOKE_TARGET)),$(MAKE) -C "$(REPO_ROOT)" --no-print-directory cabinet-guard)
	$(Q)$(if $(filter vm,$(SMOKE_TARGET)),$(VM_NEED_UP); $(VM_GATE)) \
	$(CABINET_LOCK) $(PYTHON) "$(REPO_ROOT)/tests/smoke/smoke_launch.py" --target $(SMOKE_TARGET) --out "$(SMOKE_OUT)" \
	  --ssh "$(CABINET_SSH)" --limactl "$(LIMACTL)" --shot "$(SHOT)" $(if $(SYSTEMS),--systems "$(SYSTEMS)") $(SMOKE_PYTHON_ARGS)

endif
