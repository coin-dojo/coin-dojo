#!/usr/bin/env bash
# run_spike.sh HIDE OUTDIR "STEPS"   (HIDE=none|exclusive|override|romdir) - spike only, see SPIKE.md
set -euo pipefail
H="$(cd "$(dirname "$0")" && pwd)"; ROOT="$H/../../.."
HIDE="$1"; OUT="$2"; STEPS="$3"
CACHE="${HK_CACHE:-$HOME/.cache/coin-dojo}/preview"; SCR="$CACHE/spike-$HIDE-$$"
mkdir -p "$SCR/out" "$OUT"; OUT="$(cd "$OUT" && pwd)"
~/.venvs/badges/bin/python "$H/build_spike.py" "$SCR" "$ROOT/themes/coin-dojo" "$HIDE"
IMG=coin-dojo-esde-spike:1
docker image inspect $IMG >/dev/null 2>&1 || docker build -q -t $IMG "$H" >/dev/null
N=spike-$$
trap 'docker rm -f $N >/dev/null 2>&1 || true; rm -rf "$SCR"' EXIT
docker run --name $N --platform linux/arm64 -e STEPS="$STEPS" \
  -v "$CACHE/es-de/squashfs-root":/es:ro -v "$SCR/home":/preview/home -v "$SCR/roms":/preview/roms:ro \
  -v "$SCR/theme":/preview/theme:ro -v "$SCR/out":/preview/out -v "$H":/spike:ro -v "$ROOT/skills/collections/launch.py":/launch.py:ro \
  $IMG /spike/run-steps.sh | grep '^RUN' || true
cp "$SCR"/out/* "$OUT"/ 2>/dev/null || true
