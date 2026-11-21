#!/bin/sh
# cpu-gate (retro 2026-11-15 #3): wait until total Mac CPU use (user+sys, 3 s sample) is below CPU_MAX
# (default 80, the maintainer 2026-11-14), logging every 30 s; then exit 0. CPU_GATE_TRIES=n (default 10 = 5 minutes)
# gives up after n waits: it logs "going anyway" and exits 0 (a busy laptop must not stall work forever);
# CPU_GATE_TRIES=0 waits forever. CPU_GATE_ON_TIMEOUT=go|fail (default go) says what giving up means: fail logs
# "giving up" to stderr and exits 1 (mk/vm.mk: a VM step must not start on a busy Mac).
# CPU_GATE_USED=n replaces the measurement (tests); CPU_GATE_SLEEP=s the 30 s.
MAX=${CPU_MAX:-80}
TRIES=${CPU_GATE_TRIES:-10}
NAP=${CPU_GATE_SLEEP:-30}
ON_TIMEOUT=${CPU_GATE_ON_TIMEOUT:-go}
case "$ON_TIMEOUT" in go|fail) ;; *) echo "cpu-gate: CPU_GATE_ON_TIMEOUT must be go or fail, not $ON_TIMEOUT" >&2; exit 2 ;; esac
n=0
while :; do
  if [ -n "${CPU_GATE_USED:-}" ]; then used=$CPU_GATE_USED
  else used=$(top -l 2 -s 3 -n 0 | awk '/CPU usage/ {u=$3; s=$5} END {gsub("%","",u); gsub("%","",s); printf "%d", u+s}'); fi
  [ "${used:-100}" -lt "$MAX" ] && { echo "cpu-gate: ${used}% < ${MAX}%, go"; exit 0; }
  n=$((n + 1))
  if [ "$TRIES" -gt 0 ] && [ "$n" -ge "$TRIES" ]; then
    if [ "$ON_TIMEOUT" = fail ]; then echo "cpu-gate: still ${used}% after $n waits, giving up" >&2; exit 1; fi
    echo "cpu-gate: still ${used}% after $n waits, going anyway"; exit 0
  fi
  echo "cpu-gate: ${used}% >= ${MAX}%, waiting ${NAP} s"; sleep "$NAP"
done
