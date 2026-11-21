#!/usr/bin/env bash
# rollout (#143): deploy with proof (make rollout [APPLY=1] [SMOKE=1] [BECOME=1 TAGS= LIMIT=]).
#   1. make deploy (dry run), log 1-dry.log
#   APPLY=1 only:
#   2. make deploy APPLY=1, log 2-apply.log
#   3. make deploy (dry run again), log 3-dry.log; tools/recap.py --zero on it: it must recap changed=0, failed=0,
#      unreachable=0 (idempotence), else FAIL
#   4. make esde-verify (read-only)       5. SMOKE=1: make smoke-launch
# Logs go to $HK_CACHE/rollout/<stamp>/. A failed step stops the rollout; nothing is retried.
# tools/recap.py contract (#136): `recap.py --zero LOG` exits 0 (all zero), 1 (changed/failed/unreachable > 0,
# named), 2 (missing or malformed log).
# Env: ROLLOUT_MAKE (the make command, default `make --no-print-directory -C <repo>`; tests use a stub),
#   ROLLOUT_RECAP (default tools/recap.py), APPLY, SMOKE, HK_CACHE. Flags like BECOME/TAGS/LIMIT reach the nested
#   make through MAKEFLAGS when this runs from make.
# Exit 0 = PASS, 1 = a step failed or the second dry run was not zero, 2 = usage / recap.py missing.
set -uo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
MAKE_CMD=${ROLLOUT_MAKE:-make --no-print-directory -C $REPO}
RECAP=${ROLLOUT_RECAP:-$REPO/tools/recap.py}
APPLY=${APPLY:-0}
SMOKE=${SMOKE:-0}
HK_CACHE=${HK_CACHE:-$HOME/.cache/coin-dojo}
case $APPLY in 0|1) ;; *) echo "rollout: APPLY must be 0 or 1" >&2; exit 2 ;; esac
case $SMOKE in 0|1) ;; *) echo "rollout: SMOKE must be 0 or 1" >&2; exit 2 ;; esac
if [ "$APPLY" = 1 ] && [ ! -f "$RECAP" ]; then
  echo "RESULT: FAIL rollout: $RECAP is missing (needs tools/recap.py, #136); nothing was applied"; exit 2
fi
OUT=$HK_CACHE/rollout/$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$OUT" || { echo "RESULT: FAIL rollout: cannot create $OUT"; exit 2; }

step() {   # step NAME LOG ARGS...: run make ARGS, tee to OUT/LOG, stop the rollout on failure
  local name=$1 log=$2; shift 2
  echo "== rollout: $name (log $OUT/$log)"
  # shellcheck disable=SC2086  # MAKE_CMD is a command line
  $MAKE_CMD "$@" 2>&1 | tee "$OUT/$log"
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then echo "RESULT: FAIL rollout: $name exited $rc (log $OUT/$log)"; exit 1; fi
}

step "dry run" 1-dry.log deploy APPLY=0
if [ "$APPLY" != 1 ]; then
  echo "DRY RUN rollout: the dry run above is what would change. APPLY=1 applies it, dry-runs again (must recap changed=0) and verifies."
  echo "RESULT: PASS rollout (dry run only; log $OUT/1-dry.log)"; exit 0
fi
step "apply" 2-apply.log deploy APPLY=1
step "second dry run" 3-dry.log deploy APPLY=0
echo "== rollout: recap of the second dry run"
python3 "$RECAP" --zero "$OUT/3-dry.log"; rc=$?
case $rc in
  0) ;;
  1) echo "RESULT: FAIL rollout: the second dry run still changes things (not idempotent; log $OUT/3-dry.log)"; exit 1 ;;
  *) echo "RESULT: FAIL rollout: recap.py could not read $OUT/3-dry.log (exit $rc)"; exit 1 ;;
esac
step "esde-verify" 4-verify.log esde-verify
if [ "$SMOKE" = 1 ]; then step "smoke-launch" 5-smoke.log smoke-launch; fi
echo "RESULT: PASS rollout (applied, second dry run changed=0, esde-verify OK$([ "$SMOKE" = 1 ] && echo ', smoke OK'); logs $OUT)"
