#!/bin/bash
# Check a rendered overlay in the cabinet's RetroArch (1.20, software GL, no cabinet needed).
# Usage: overlay-preview.sh <core.so dir> <rom file> <overlays dir> <override.cfg> <out dir>
# All paths under $HOME (Colima shares only $HOME). ROM and core are your own copies; nothing is committed.
set -euo pipefail
[ $# -eq 5 ] || { echo "usage: $0 <core dir> <rom> <overlays dir> <override.cfg> <out dir>" >&2; exit 2; }
D="$(cd "$(dirname "$0")" && pwd)"
TAG=coin-dojo-ra-overlay:1
docker image inspect "$TAG" >/dev/null 2>&1 || docker build -q --platform linux/arm64 -t "$TAG" "$D" >/dev/null
mkdir -p "$5"
docker run --rm --platform linux/arm64 -e ROM="$(basename "$2")" -e BOOT="${BOOT:-20}" \
  -v "$1":/core:ro -v "$(dirname "$2")":/rom:ro -v "$3":/data/coin-dojo/controls/overlays:ro \
  -v "$4":/override.cfg:ro -v "$5":/out -v "$D/run-ra.sh":/run-ra.sh:ro "$TAG" bash /run-ra.sh
# open = card visible and the game frozen; closed = card gone and the game moving again
read -r SHOWN PAUSED RUNNING < "$5/diff.txt"
echo "pixels changed: card shown vs running $SHOWN, while open $PAUSED, after close $RUNNING"
# open = card visible and the game frozen; closed = card gone and the game moving again
if awk -v s="$SHOWN" -v p="$PAUSED" -v r="$RUNNING" 'BEGIN { exit !(s+0 > 100000 && p+0 == 0 && r+0 > 0) }'; then
  echo "RESULT: PASS"
else
  echo "RESULT: FAIL"; exit 1
fi
