#!/bin/bash
# RetroArch 1.22.2 overlay check (cabinet build, Wayland/GLES, no X11) in a headless cage session.
# Usage: overlay-preview-122.sh <ra122 dir (retroarch + BUILDINFO)> <core dir> <rom file> <overlays dir> <override.cfg> <out dir>
# All paths under $HOME (Colima shares only $HOME). Binary, core, ROM are your own copies; nothing is committed.
# Exit: 0 PASS, 1 overlay rule FAIL, 2 bad usage / wrong binary, 3 the session did not start (named error, #175).
set -euo pipefail
[ $# -eq 6 ] || { echo "usage: $0 <ra122 dir> <core dir> <rom> <overlays dir> <override.cfg> <out dir>" >&2; exit 2; }
D="$(cd "$(dirname "$0")" && pwd)"
grep -q 'tag: v1.22.2' "$1/BUILDINFO" 2>/dev/null || { echo "$1/BUILDINFO must say v1.22.2" >&2; exit 2; }
# The unpatched build segfaults in eglChooseConfig's loop under cage/llvmpipe (#175); the cabinet runs the patched one.
grep -q '^patches:.*0001-egl-loop-only-matched-configs' "$1/BUILDINFO" || {
  echo "$1/BUILDINFO lists no 0001-egl-loop-only-matched-configs patch: unpatched RetroArch segfaults at EGL;" \
       "copy the cabinet's patched retroarch + BUILDINFO (or build/retroarch/out/)" >&2; exit 2; }
TAG=coin-dojo-ra122:1
docker image inspect "$TAG" >/dev/null 2>&1 || docker build -q --platform linux/arm64 -t "$TAG" "$D" >/dev/null
mkdir -p "$6"
rm -f "$6/startup.txt" "$6/diff.txt"
rc=0
docker run --rm --platform linux/arm64 -e ROM="$(basename "$3")" -e BOOT="${BOOT:-20}" -e READY_WAIT="${READY_WAIT:-60}" \
  -v "$1":/ra:ro -v "$2":/core:ro -v "$(dirname "$3")":/rom:ro -v "$4":/data/coin-dojo/controls/overlays:ro \
  -v "$5":/override.cfg:ro -v "$6":/out -v "$D/run-ra122.sh":/run-ra122.sh:ro "$TAG" bash /run-ra122.sh || rc=$?
STARTUP=$(cat "$6/startup.txt" 2>/dev/null || echo "NO_VERDICT: the run wrote no startup.txt (docker exit $rc)")
echo "startup: $STARTUP"
if [ "$rc" -ne 0 ] || [ "${STARTUP%% *}" != alive ]; then
  echo "ERROR: the preview did not start: $STARTUP (logs: $6/cage.log, $6/ra.log)" >&2
  echo "RESULT: FAIL"; exit 3
fi
read -r SHOWN PAUSED RUNNING < "$6/diff.txt" || true
for n in "${SHOWN:-}" "${PAUSED:-}" "${RUNNING:-}"; do
  [[ "$n" =~ ^[0-9.e+]+$ ]] || { echo "ERROR: DIFF_FAILED: diff.txt is not three numbers: $(cat "$6/diff.txt" 2>/dev/null)" >&2
                                 echo "RESULT: FAIL"; exit 3; }
done
echo "pixels changed: card shown vs running $SHOWN, while open $PAUSED, after close $RUNNING"
if awk -v s="$SHOWN" -v p="$PAUSED" -v r="$RUNNING" 'BEGIN { exit !(s+0 > 100000 && p+0 == 0 && r+0 > 0) }'; then
  echo "RESULT: PASS"
else
  echo "RESULT: FAIL"; exit 1
fi
