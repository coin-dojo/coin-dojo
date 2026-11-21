#!/bin/bash
# coin-dojo-vm smoke test (#82), run as coindojo inside the VM (make vm-smoke). Installed by vm_base to
# /usr/local/lib/coin-dojo-vm/smoke.sh next to mknesrom.py and shotcheck.py.
#
#   smoke.sh [OUT_DIR]        default OUT_DIR=/tmp/coin-dojo-smoke
#
# 1. cage (headless, pixman) -> ES-DE with the cabinet's flags; at 10 s cage + ES-DE alive, and a
#    grim screenshot that is not blank (shotcheck --ui).
# 2. RetroArch (RA, default the 1.22.2 build that retroarch122 installs; RA=/usr/bin/retroarch for
#    Debian's 1.20) + the Nestopia core + a generated test ROM (our own code, never a game),
#    started on the same display the way ES-DE starts it; at 10 s cage + ES-DE + RetroArch alive,
#    and >= 30% of the screenshot is the ROM's red (the core really runs the content).
# 3. The same ROM through ~/ES-DE/scripts/launch.py (LAUNCH=), as ES-DE starts every game (#62): its dry
#    run must name the 1.22 binary + nestopia, then the real launch must show the red screen.
# 4. Everything is stopped again (the VM idles at ~0% CPU afterwards).
# Prints RESULT lines; the last is `RESULT: PASS vm-smoke` or `RESULT: FAIL vm-smoke: <steps>`.
set -u
OUT=${1:-/tmp/coin-dojo-smoke}
LIB=/usr/local/lib/coin-dojo-vm
RA=${RA:-/data/emulators/retroarch-1.22/retroarch}
CORE=${CORE:-/usr/lib/aarch64-linux-gnu/libretro/nestopia_libretro.so}
ROM=/data/roms/nes/coin-dojo-smoke.nes
LAUNCH=${LAUNCH:-$HOME/ES-DE/scripts/launch.py}
WAIT=${SMOKE_WAIT:-10}
fails=""
fail() { echo "RESULT: FAIL $*"; fails="$fails $1"; }
pass() { echo "RESULT: PASS $*"; }
alive() { pgrep -u "$(id -u)" -x "$1" >/dev/null; }
es_alive() { alive es-de || alive AppRun; }

rm -rf "$OUT"; mkdir -p "$OUT"
hk-session stop >/dev/null
python3 -I "$LIB/mknesrom.py" "$ROM" || fail rom "could not write $ROM"

# --- 1. ES-DE ----------------------------------------------------------------------------------
t0=$(date +%s)
HK_LOG=$OUT/session.log hk-session start || fail session "cage did not start"
sleep "$WAIT"
if alive cage && es_alive; then pass "es-de: cage + ES-DE alive at ${WAIT} s"; else fail es-de "cage/ES-DE died within ${WAIT} s (see $OUT/session.log)"; fi
hk-session shot "$OUT/es-de" >/dev/null && [ -s "$OUT/es-de.png" ] && python3 -I "$LIB/shotcheck.py" "$OUT/es-de.ppm" --ui \
  || fail es-de-shot "ES-DE screenshot empty or blank"

# --- 2. RetroArch + core + test ROM, on top of ES-DE ------------------------------------------
if [ ! -x "$RA" ]; then
  fail retroarch "$RA missing (run make vm-provision; RA=/usr/bin/retroarch for Debian 1.20)"
elif [ ! -f "$CORE" ]; then
  fail core "$CORE missing (vm_base installs libretro-nestopia)"
else
  echo "retroarch: $("$RA" --version 2>&1 | grep -m1 -oE 'v?1\.[0-9]+\.[0-9]+' || echo '?') $RA"
  hk-session run "$RA" --verbose -L "$CORE" "$ROM" >/dev/null
  sleep "$WAIT"
  if alive cage && es_alive && alive retroarch; then pass "retroarch: cage + ES-DE + RetroArch alive at ${WAIT} s"
  else fail retroarch "a process died within ${WAIT} s: cage=$(alive cage && echo up || echo down) es-de=$(es_alive && echo up || echo down) retroarch=$(alive retroarch && echo up || echo down) (see $OUT/session.log)"; fi
  hk-session shot "$OUT/retroarch" >/dev/null && [ -s "$OUT/retroarch.png" ] && python3 -I "$LIB/shotcheck.py" "$OUT/retroarch.ppm" --red 0.30 \
    || fail retroarch-shot "the test ROM's red screen is not showing"
fi

# --- 3. the same ROM THROUGH launch.py, the way ES-DE's virtual systems start every game (#62) ---
# launch.py must pick the 1.22 binary and the nes core by itself (no COIN_DOJO_* relocation).
if [ ! -f "$LAUNCH" ]; then
  fail launch.py "$LAUNCH missing (make deploy-launch LIMIT=coin-dojo-vm APPLY=1, or make vm-provision)"
else
  pkill -u "$(id -u)" -x retroarch 2>/dev/null; sleep 1
  dry=$(env -u COIN_DOJO_RETROARCH -u COIN_DOJO_CORES -u COIN_DOJO_ROMS python3 "$LAUNCH" --dry-run "$ROM" 2>&1)
  echo "launch.py: $dry"
  case $dry in
    "DRYRUN system=nes cmd=/data/emulators/retroarch-1.22/retroarch -L "*nestopia_libretro.so" $ROM") pass "launch.py: dry run builds the 1.22 command" ;;
    *) fail launch-dry "launch.py --dry-run did not build the 1.22 nes command" ;;
  esac
  hk-session run env -u COIN_DOJO_RETROARCH -u COIN_DOJO_CORES -u COIN_DOJO_ROMS python3 "$LAUNCH" "$ROM" >/dev/null
  sleep "$WAIT"
  ra_cmd=$(pgrep -u "$(id -u)" -a -x retroarch | head -1)
  if alive cage && es_alive && [ -n "$ra_cmd" ]; then pass "launch.py: RetroArch alive at ${WAIT} s ($ra_cmd)"
  else fail launch "RetroArch via launch.py died within ${WAIT} s (see $OUT/session.log)"; fi
  case $ra_cmd in *"/data/emulators/retroarch-1.22/retroarch -L "*) ;; *) fail launch-binary "not the 1.22 binary: $ra_cmd" ;; esac
  hk-session shot "$OUT/launch" >/dev/null && [ -s "$OUT/launch.png" ] && python3 -I "$LIB/shotcheck.py" "$OUT/launch.ppm" --red 0.30 \
    || fail launch-shot "the test ROM's red screen is not showing (via launch.py)"
fi

hk-session stop >/dev/null
rm -f "$OUT"/*.ppm
echo "smoke: $(( $(date +%s) - t0 )) s, screenshots in $OUT"
if [ -n "$fails" ]; then echo "RESULT: FAIL vm-smoke:$fails"; exit 1; fi
echo "RESULT: PASS vm-smoke"
