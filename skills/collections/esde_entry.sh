#!/bin/bash
# esde_entry.sh (#214): the launch command of the ES-DE systems FIGHTCADE and QUIT TO MAIN MENU.
#
#   esde_entry.sh fightcade|menu [ROM]     (ES-DE appends the entry's path as ROM; it is ignored)
#
# Shipped by the esde_collections role to /data/coin-dojo/esde_entry.sh (the systems run it with `bash`, the tools are
# copied 0644). Contract (#204 "Interfaces v2", session role #209):
#   1. refuse while a game runs (retroarch, flycast: the deploy guard's names, esde_game_procs/esde_game_patterns):
#      log + stderr, write nothing, end nothing, exit 0 (ES-DE has no message box for a command; it just returns to
#      the list). FIGHTCADE also refuses when /usr/local/bin/coin-dojo-fightcade is missing (mode not deployed).
#   2. write the mode atomically to $XDG_RUNTIME_DIR/coin-dojo/next-mode (dir made 0700). Without XDG_RUNTIME_DIR the
#      session's own fallback /run/user/<uid> is used, only if that directory exists; else refuse (exit 0).
#   3. end ES-DE: coin-dojo-session-stop (signals the session, which TERMs ES-DE, cleans up and reads next-mode; it
#      leaves our `fightcade` alone because it only writes `menu` when no request exists). Without that helper
#      (session role not deployed): TERM ES-DE itself.
#   4. exit 0. Only a wrong usage exits 2.
# Env (tests): HK_ENTRY_STOP (the stop helper), HK_ENTRY_FIGHTCADE (the Fightcade launcher), HK_ENTRY_LOG.
# Bash 3.2 compatible.
set -u

mode=${1:-}
case "$mode" in
  fightcade | menu) ;;
  *) echo "usage: esde_entry.sh fightcade|menu [ROM]" >&2; exit 2 ;;
esac

STOP=${HK_ENTRY_STOP:-/usr/local/bin/coin-dojo-session-stop}
FIGHTCADE=${HK_ENTRY_FIGHTCADE:-/usr/local/bin/coin-dojo-fightcade}
LOG=${HK_ENTRY_LOG:-${HOME:-/tmp}/.cache/coin-dojo/esde-entry.log}

say() {
  mkdir -p "$(dirname "$LOG")" 2>/dev/null
  printf '%s esde_entry[%s]: %s\n' "$(date '+%F %T')" "$mode" "$*" >>"$LOG" 2>/dev/null
  printf 'esde_entry: %s\n' "$*" >&2
}

# 1. a game runs: refuse
for name in retroarch retroarch122 flycast; do
  if pgrep -x "$name" >/dev/null 2>&1; then
    say "refused: $name is running, quit the game first"
    exit 0
  fi
done
if pgrep -f 'retroarch-1.22/retroarc[h]' >/dev/null 2>&1; then
  say "refused: RetroArch 1.22 is running, quit the game first"
  exit 0
fi
if [ "$mode" = fightcade ] && [ ! -x "$FIGHTCADE" ]; then
  say "refused: Fightcade mode is not installed ($FIGHTCADE missing)"
  exit 0
fi

# 2. the request
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
  base=$XDG_RUNTIME_DIR
else
  base=/run/user/$(id -u)
  if [ ! -d "$base" ]; then
    say "refused: no XDG_RUNTIME_DIR and no $base"
    exit 0
  fi
fi
dir=$base/coin-dojo
if ! (umask 077 && mkdir -p "$dir") 2>/dev/null; then
  say "refused: cannot create $dir"
  exit 0
fi
chmod 700 "$dir" 2>/dev/null
tmp=$dir/next-mode.$$
if ! { printf '%s\n' "$mode" >"$tmp" && mv -f "$tmp" "$dir/next-mode"; } 2>/dev/null; then
  rm -f "$tmp"
  say "refused: cannot write $dir/next-mode"
  exit 0
fi
say "requested $mode"

# 3. end ES-DE
if [ -x "$STOP" ]; then
  "$STOP" || say "$STOP failed (rc $?)"
else
  uid=$(id -u)
  pids=$(pgrep -x -u "$uid" 'es-de|AppRun' 2>/dev/null)
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null
    say "TERM sent to ES-DE ($pids)"
  else
    say "ES-DE not found, nothing to end"
  fi
fi
exit 0
