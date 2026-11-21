#!/bin/sh
# Thin wrapper: uses the venv at $BADGE_VENV (default ~/.venvs/badges) if present.
# Usage: badge.sh OUT TEXT [OUT TEXT ...]
V="${BADGE_VENV:-$HOME/.venvs/badges}"
PY=python3; [ -x "$V/bin/python" ] && PY="$V/bin/python"
exec "$PY" "$(dirname "$0")/badge.py" "$@"
