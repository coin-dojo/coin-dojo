#!/usr/bin/env bash
# INSIDE the container. Env STEPS: space separated; shot:NAME = screenshot, sleep:N, else xdotool key.
set -uo pipefail
W=1440 H=1080
export HOME=/preview/home LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe SDL_VIDEODRIVER=x11 DISPLAY=:99
OUT=/preview/out; LOG=$HOME/ES-DE/logs/es_log.txt
Xvfb :99 -screen 0 ${W}x${H}x24 +extension GLX +render -noreset >"$OUT/xvfb.log" 2>&1 &
XV=$!
for _ in $(seq 1 20); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
/es/AppRun --home /preview/home --resolution $W $H >"$OUT/es-stdout.log" 2>&1 &
ES=$!
trap 'kill $ES $XV 2>/dev/null; cp "$LOG" "$OUT/es_log.txt" 2>/dev/null' EXIT
for _ in $(seq 1 120); do
  kill -0 $ES 2>/dev/null || { echo "RUN: FAIL es-de exited early"; exit 3; }
  grep -q "Application startup time" "$LOG" 2>/dev/null && break; sleep 1
done
sleep 8
for s in $STEPS; do
  case "$s" in
    shot:*) import -window root "$OUT/${s#shot:}.png" && echo "RUN: wrote ${s#shot:}.png" ;;
    sleep:*) sleep "${s#sleep:}" ;;
    *) xdotool key "$s"; sleep 2.5 ;;
  esac
done
exit 0
