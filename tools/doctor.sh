#!/usr/bin/env bash
# doctor (#143): is this Mac ready to run coin-dojo? (make doctor). Read-only; one PASS/WARN/FAIL line per check.
#   FAIL: python3 >= 3.9, the config python (PyYAML), the configuration resolves (config.py check), the ssh key,
#         docker CLI + a running docker (Colima).
#   WARN: limactl (test VM only), gum (make commit only), the Keychain item for sudo is present (its value is never
#         read: no -w), old ~/.cache/coin-dojo-* folders (make cache-migrate).
# Env (set by make): HK_REPO, CONFIG_PYTHON, CFG_LIMIT (default coin-dojo), HK_CACHE. Tests put stubs first on PATH.
# Exit 0 = no FAIL, 1 = at least one FAIL. The last line is the verdict.
set -uo pipefail
REPO=${HK_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
PY=${CONFIG_PYTHON:-python3}
LIMIT=${CFG_LIMIT:-coin-dojo}
HK_CACHE=${HK_CACHE:-$HOME/.cache/coin-dojo}
fails=0; warns=0
pass() { printf 'PASS %-10s %s\n' "$1" "$2"; }
warn() { printf 'WARN %-10s %s\n' "$1" "$2"; warns=$((warns + 1)); }
fail() { printf 'FAIL %-10s %s\n' "$1" "$2"; fails=$((fails + 1)); }
cfg() { "$PY" "$REPO/tools/config.py" "$@"; }

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  pass python "$(python3 -c 'import platform; print(platform.python_version())')"
else fail python "python3 3.9 or newer is needed"; fi

if "$PY" -c 'import yaml' 2>/dev/null; then pass venv "$PY has PyYAML"
else fail venv "$PY has no PyYAML: make setup"; fi

cfg_ok=0
if out=$(cfg check --limit "$LIMIT" 2>&1); then pass config "resolves for $LIMIT"; cfg_ok=1
else fail config "$(printf '%s\n' "$out" | grep -v '^RESULT' | head -n 3 | tr '\n' ' ')(make config-check; setup copies config/local.example.yml)"; fi

if [ "$cfg_ok" = 1 ]; then
  key=$(cfg get --limit "$LIMIT" hk_ssh_key 2>/dev/null)
  # shellcheck disable=SC2088  # a literal ~/ from the config, expanded by hand
  case $key in "~/"*) key=$HOME/${key#\~/} ;; esac
  if [ -n "$key" ] && [ -f "$key" ]; then pass ssh-key "$key"; else fail ssh-key "no private key at ${key:-?} (hk_ssh_key)"; fi
  kc=$(cfg get --limit "$LIMIT" hk_become_keychain 2>/dev/null)
  if ! command -v security >/dev/null 2>&1; then warn keychain "no macOS security tool: BECOME=1 asks for the sudo password"
  elif [ -n "$kc" ] && security find-generic-password -s "$kc" >/dev/null 2>&1; then pass keychain "item $kc present"
  else warn keychain "Keychain item ${kc:-?} missing: BECOME=1 asks for the sudo password"; fi
else
  fail ssh-key "not checked: the config does not resolve"
fi

if ! command -v docker >/dev/null 2>&1; then fail docker "no docker CLI (brew install docker colima)"
elif docker info >/dev/null 2>&1; then pass docker "running"
else fail docker "not running (colima start)"; fi
if command -v limactl >/dev/null 2>&1; then pass limactl "$(command -v limactl)"; else warn limactl "missing: only the test VM needs it (brew install lima)"; fi
if command -v gum >/dev/null 2>&1; then pass gum "$(command -v gum)"; else warn gum "missing: only make commit needs it (brew install gum)"; fi

old=""
for n in ansible preview vm ws; do [ -d "$HOME/.cache/coin-dojo-$n" ] && old="$old coin-dojo-$n"; done
if [ -n "$old" ]; then warn cache "old folders in ~/.cache:$old (make cache-migrate)"; else pass cache "$HK_CACHE"; fi

if [ "$fails" -gt 0 ]; then echo "RESULT: FAIL doctor: $fails failure(s), $warns warning(s)"; exit 1; fi
echo "RESULT: PASS doctor ($warns warning(s))"
