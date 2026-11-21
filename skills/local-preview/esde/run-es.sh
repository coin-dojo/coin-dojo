#!/usr/bin/env bash
# Runs INSIDE the preview container. Mounts: /es (extracted AppImage), /preview/{home,roms,media,theme,out}.
# Env: VIEW=system|gamelist|both, KEYS (xdotool keys pressed to reach the gamelist, default Return),
#      SETTLE / SETTLE_NAV (seconds to let software GL draw).
# Xvfb + llvmpipe: see SKILL.md "ES-DE theme preview" for why not a Wayland compositor.
set -uo pipefail
W=1440 H=1080
VIEW=${VIEW:-both}
export HOME=/preview/home LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe SDL_VIDEODRIVER=x11 DISPLAY=:99
OUT=/preview/out
LOG=$HOME/ES-DE/logs/es_log.txt
Xvfb :99 -screen 0 ${W}x${H}x24 +extension GLX +render -noreset >"$OUT/xvfb.log" 2>&1 &
XV=$!
for _ in $(seq 1 20); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
/es/AppRun --home /preview/home --resolution $W $H >"$OUT/es-stdout.log" 2>&1 &
ES=$!
# shellcheck disable=SC2317  # called by the trap below
cleanup() { kill "$ES" "$XV" 2>/dev/null; cp "$LOG" "$OUT/es_log.txt" 2>/dev/null; }
trap cleanup EXIT

# wait until ES-DE has finished starting (the log's last startup line), then let the first frame draw
for _ in $(seq 1 120); do
  kill -0 $ES 2>/dev/null || { echo "RUN: FAIL es-de exited early (see es-stdout.log)" >&2; exit 3; }
  grep -q "Application startup time" "$LOG" 2>/dev/null && break
  sleep 1
done
grep -q "Application startup time" "$LOG" 2>/dev/null || { echo "RUN: FAIL es-de did not finish starting in 120 s" >&2; exit 3; }
sleep "${SETTLE:-8}"
shot() { import -window root "$OUT/$1.png" && echo "RUN: wrote $1.png"; }
[ "$VIEW" = gamelist ] || shot system
if [ "$VIEW" != system ]; then
  for k in ${KEYS:-Return}; do xdotool key "$k"; sleep 1; done
  sleep "${SETTLE_NAV:-5}"
  kill -0 $ES 2>/dev/null || { echo "RUN: FAIL es-de died after navigation" >&2; exit 3; }
  shot gamelist
fi
exit 0
