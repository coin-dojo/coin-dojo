#!/bin/bash
# smoke_remote.sh - the target-side half of `make smoke-launch` (#48). smoke_launch.py pipes it into
# `bash -s -- <step> ...` on the cabinet (ssh), in the VM (limactl shell) or locally (tests). Plain
# bash 3.2 + coreutils + grim + python3, the same as the cabinet has. It prints key=value lines.
#
#   guard             refuse (exit 9) when retroarch/flycast runs, (exit 8) when ES-DE is not up
#   prep              back up retroarch.cfg + flycast emu.cfg to $HK_BAK and record their sha256; refuses
#                     (exit 1) when $HK_BAK holds an interrupted run's backup whose config is still changed
#   one SYSTEM ROM    launch ONE game THROUGH launch.py (what ES-DE runs), on top of ES-DE; at $HK_WAIT s
#                     and again $HK_GAP s later the process must be alive; grim takes two shots; then
#                     SIGKILL the whole process group. A RetroArch run goes through a wrapper that
#                     adds `--appendconfig` whose FIRST line is config_save_on_exit = "false", so the
#                     real retroarch.cfg is never rewritten. Output: result=RUN|SKIP|FAIL + state/rc/shots.
#   attract-off       with no game running: ScreensaverTimer in es_settings.xml -> 0 (an in-place edit of that one
#                     line, NEVER a copy of the file: it holds the ScreenScraper password), then restart ES-DE
#                     (pkill -9 -x AppRun, the deploy handler's way; ES-DE rewrites es_settings.xml from memory on a
#                     normal exit, so only a kill keeps the edit), wait until AppRun is back and its log is fresh,
#                     check getty@tty1. The ORIGINAL value is kept in $HK_BAK/screensaver.orig (the number only).
#                     Exit 9 = a game runs (nothing touched), 1 = failed (the original is kept for the restore).
#   attract-restore   put the original value back the same way (also called by finish and by the signal trap of
#                     attract-off / one). A foreign game: no restart, prints the exact command instead.
#   finish            compare both configs with the backup (restore + report a change), leftover check, then
#                     delete $HK_OUT (the controller has pulled everything) and $HK_BAK unless a config changed
#
# Never kills by NAME (the maintainer may start a game mid-run): a leftover is only a retroarch/flycast pid whose
# process group is one of our jobs' groups (recorded in $HK_OUT/jobs). Any other pid is FOREIGN: reported
# as `foreign=<pids>` (exit 9, "a game started during the run"), killed never, and no config is restored
# once one was seen (the config may be the maintainer's own game's doing).
#
# Environment (defaults are the cabinet's): HK_LAUNCH (~/ES-DE/scripts/launch.py), HK_ROMS (/data/roms),
# HK_OUT (/tmp/hk-smoke), HK_BAK (/tmp/hk-smoke-bak), HK_WAIT (10), HK_GAP (3), HK_SETTLE (0.5, s after the kill), HK_SHOT (full|small: kept png at native size or half; default full), HK_MISSING (FAIL|SKIP:
# what a missing ROM/binary/core counts as; the VM uses SKIP), HK_RA_CFG, HK_FLY_CFG, XDG_RUNTIME_DIR,
# WAYLAND_DISPLAY (default: the VM session's file, else wayland-0), HK_ES_SETTINGS (~/ES-DE/settings/es_settings.xml),
# HK_ESDE_LOG (~/ES-DE/logs/es_log.txt), HK_ESDE_WAIT (poll rounds for ES-DE to come back, 40), HK_ESDE_POLL (1 s),
# HK_ESDE_GONE (3 s between the kill and the first poll). Tests only: HK_READY_FILE, HK_READY_POLLS (see `one`).
set -u
umask 077

export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -s "$XDG_RUNTIME_DIR/hk-session.display" ]; then
  WAYLAND_DISPLAY=$(cat "$XDG_RUNTIME_DIR/hk-session.display")
fi
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}
LAUNCH=${HK_LAUNCH:-$HOME/ES-DE/scripts/launch.py}
export COIN_DOJO_ROMS=${HK_ROMS:-/data/roms}
OUT=${HK_OUT:-/tmp/hk-smoke}
BAK=${HK_BAK:-/tmp/hk-smoke-bak}
WAIT=${HK_WAIT:-10}
GAP=${HK_GAP:-3}
SETTLE=${HK_SETTLE:-0.5}
MISSING=${HK_MISSING:-FAIL}
RA_CFG=${HK_RA_CFG:-$HOME/.config/retroarch/retroarch.cfg}
FLY_CFG=${HK_FLY_CFG:-$HOME/.config/flycast/emu.cfg}
ES=${HK_ES_SETTINGS:-$HOME/ES-DE/settings/es_settings.xml}
ESDE_LOG=${HK_ESDE_LOG:-$HOME/ES-DE/logs/es_log.txt}
ESDE_WAIT=${HK_ESDE_WAIT:-40}
ESDE_POLL=${HK_ESDE_POLL:-1}
ESDE_GONE=${HK_ESDE_GONE:-3}

# `timeout` is coreutils: the cabinet and the VM have it, the Mac (local test runs) may not.
command -v timeout >/dev/null 2>&1 || timeout() { shift; "$@"; }

kv() { printf '%s=%s\n' "$1" "$(printf '%s' "$2" | tr '\n' ' ')"; }
sha() { if [ -f "$1" ]; then (sha256sum "$1" 2>/dev/null || shasum -a 256 "$1") | cut -d' ' -f1; else echo absent; fi; }
busy() { { pgrep -x retroarch; pgrep -x flycast; } 2>/dev/null | tr '\n' ' ' | sed 's/ *$//'; }
esde_up() { pgrep -x AppRun >/dev/null 2>&1 || pgrep -x es-de >/dev/null 2>&1; }
# kill -0 is true for a zombie (an emulator that died and was not waited for yet): look at the state.
alive() { local s; s=$(ps -o stat= -p "$1" 2>/dev/null); [ -n "$s" ] && [ "${s#Z}" = "$s" ]; }

# bash runs a trap only after the foreground command ends: a backgrounded sleep + wait lets SIGTERM/SIGHUP act at once.
nap() { sleep "$1" & wait $!; }
pgid_of() { ps -o pgid= -p "$1" 2>/dev/null | tr -d ' '; }
# Sort the running emulator pids into MINE (process group = one of our jobs) and FOREIGN (everything else,
# including a pid whose group cannot be read).
classify() {
  local p g; MINE=""; FOREIGN=""
  for p in $(busy); do
    g=$(pgid_of "$p")
    if [ -n "$g" ] && grep -qx "$g" "$OUT/jobs" 2>/dev/null; then MINE="$MINE $p"; else FOREIGN="$FOREIGN $p"; fi
  done
  MINE=${MINE# }; FOREIGN=${FOREIGN# }
}

# --- attract mode (ES-DE's video screensaver) off for the run, the ORIGINAL value back after ----------------------
# One in-place edit of the ScreensaverTimer line: no copy of es_settings.xml anywhere (ScreenScraper password).
PYEDIT='import re,sys
p,v=sys.argv[1],sys.argv[2]
f=open(p,"r+")
s=f.read()
n,c=re.subn(r"(<int name=\"ScreensaverTimer\" value=\")[0-9]+(\")",lambda m:m.group(1)+v+m.group(2),s)
if c!=1:
    sys.exit(3)
f.seek(0)
f.write(n)
f.truncate()
f.close()'
timer_get() { sed -n 's/.*name="ScreensaverTimer" value="\([0-9]*\)".*/\1/p' "$ES" 2>/dev/null | head -n 1; }
timer_set() { python3 -c "$PYEDIT" "$ES" "$1"; }

# Kill ES-DE the way the deploy handler does (kill -9 AppRun: it cannot save its in-memory settings over our edit),
# wait until it is back and its log is fresh, check getty@tty1. rc 1 = not back / getty not active (loud, no sudo).
esde_restart() {
  local m=$BAK/restart.marker i=0 g
  mkdir -p "$BAK"; : > "$m"
  pkill -9 -x AppRun 2>/dev/null
  nap "$ESDE_GONE"
  until esde_up; do
    i=$((i + 1))
    if [ "$i" -gt "$ESDE_WAIT" ]; then kv esde_back 0; kv reason "ES-DE (AppRun) is not back $((ESDE_WAIT * ESDE_POLL)) s after the kill; see runbooks/cabinet-recovery.md 3c"; rm -f "$m"; return 1; fi
    nap "$ESDE_POLL"
  done
  kv esde_back 1
  i=0
  until [ -n "$(find "$ESDE_LOG" -newer "$m" 2>/dev/null)" ]; do
    i=$((i + 1)); [ "$i" -gt "$ESDE_WAIT" ] && break
    nap "$ESDE_POLL"
  done
  if [ -n "$(find "$ESDE_LOG" -newer "$m" 2>/dev/null)" ]; then kv esde_log fresh; else kv esde_log stale; fi
  rm -f "$m"
  g=$(systemctl is-active getty@tty1 2>/dev/null); kv getty "${g:-unknown}"
  [ "$g" = active ] || { kv reason "getty@tty1 is '${g:-unknown}' after the ES-DE restart: the cabinet session may be down (runbooks/cabinet-recovery.md 3c); no sudo tried"; return 1; }
}

attract_off() {
  local b orig
  b=$(busy)
  if [ -n "$b" ]; then kv busy "$b"; kv reason "refused: a game is running (pid $b); ES-DE and es_settings.xml untouched"; return 9; fi
  if [ ! -f "$ES" ]; then
    if [ "$MISSING" = SKIP ]; then kv attract skipped; kv reason "no $ES here"; return 0; fi
    kv reason "es_settings.xml missing: $ES"; return 1
  fi
  orig=$(timer_get)
  case $orig in ''|*[!0-9]*) kv reason "no ScreensaverTimer line (<int name=\"ScreensaverTimer\" value=\"N\" />) in $ES"; return 1;; esac
  if [ "$orig" = 0 ]; then kv attract already_off; return 0; fi
  mkdir -p "$BAK" || return 1
  printf '%s\n' "$orig" > "$BAK/screensaver.orig"
  kv screensaver_orig "$orig"
  # From here on whatever ends the step (ssh drop, signal) puts the value back.
  trap 'INTR=1; exit 130' HUP TERM INT PIPE
  trap '[ "${INTR:-}" = 1 ] && attract_restore' EXIT
  timer_set 0 || { kv reason "could not edit the ScreensaverTimer line in $ES"; rm -f "$BAK/screensaver.orig"; return 1; }
  esde_restart || return 1
  kv attract off
}

# Put the original value back and restart ES-DE the same way. Never while a game that is not ours runs.
attract_restore() {
  local f=$BAK/screensaver.orig orig lock=$BAK/restore.lock w=0 b rc
  [ -f "$f" ] || return 0
  orig=$(cat "$f")
  case $orig in ''|*[!0-9]*) kv reason "$f does not hold a number"; return 1;; esac
  classify
  if [ -n "$MINE" ]; then for b in $MINE; do kill -9 "$b" 2>/dev/null; done; sleep "$SETTLE"; classify; fi
  if [ -n "$FOREIGN" ] || [ -e "$OUT/foreign" ]; then
    kv screensaver_not_restored 1; kv screensaver_orig "$orig"
    kv screensaver_restore_cmd "sed -i 's/\\(name=\"ScreensaverTimer\" value=\"\\)[0-9]*\"/\\1$orig\"/' $ES && pkill -9 -x AppRun"
    return 9
  fi
  # Two restorers (a drop trap and the controller's finish) must not kill ES-DE twice.
  while ! mkdir "$lock" 2>/dev/null; do
    if [ -f "$lock/pid" ] && ! kill -0 "$(cat "$lock/pid")" 2>/dev/null; then rm -rf "$lock"; continue; fi
    w=$((w + 1)); [ "$w" -gt $((ESDE_WAIT * 3)) ] && { kv reason "another restore holds $lock"; return 1; }
    nap 1; [ -f "$f" ] || return 0
  done
  echo $$ > "$lock/pid"
  [ -f "$f" ] || { rm -rf "$lock"; return 0; }      # the other restorer finished while we waited
  rc=0
  if timer_set "$orig"; then esde_restart || rc=1; else kv reason "could not put ScreensaverTimer back in $ES"; rc=1; fi
  rm -rf "$lock"
  if [ "$rc" = 0 ]; then rm -f "$f"; kv screensaver_restored "$orig"; else kv screensaver_not_restored 1; kv screensaver_orig "$orig"; fi
  return $rc
}

guard() {
  local b; b=$(busy)
  if [ -n "$b" ]; then kv busy "$b"; return 9; fi
  if ! esde_up; then kv no_esde 1; return 8; fi
  kv guard ok
}

cfg_pairs() { printf '%s\n' "retroarch.cfg:$RA_CFG" "emu.cfg:$FLY_CFG"; }

prep() {
  local pair n f
  if [ -f "$BAK/screensaver.orig" ]; then
    kv reason "refused: a previous run turned ES-DE attract mode off and did not put it back (ScreensaverTimer was $(cat "$BAK/screensaver.orig")); run 'bash smoke_remote.sh attract-restore' (HK_BAK=$BAK) with no game running, or set it by hand, then rm -rf $BAK"
    return 1
  fi
  if [ -d "$BAK" ]; then
    for pair in $(cfg_pairs); do
      n=${pair%%:*}; f=${pair#*:}
      if [ -f "$BAK/$n.sha" ] && [ "$(sha "$f")" != "$(cat "$BAK/$n.sha")" ]; then
        kv reason "refused: $BAK is a previous run's backup and $f differs from it; compare them, restore $BAK/$n by hand if it is wanted, then rm -rf $BAK"
        return 1
      fi
    done
  fi
  rm -rf "$BAK" "$OUT"; mkdir -p "$BAK" "$OUT" || return 1
  : > "$OUT/jobs"
  for pair in $(cfg_pairs); do
    n=${pair%%:*}; f=${pair#*:}
    [ -f "$f" ] && cp -p "$f" "$BAK/$n"
    sha "$f" > "$BAK/$n.sha"
    kv "sha_before.$n" "$(cat "$BAK/$n.sha")"
  done
}

# Compare every config with the backup; put back what changed, unless a foreign game was seen (then only
# report: it may have written the file). Prints cfg_changed=<names> (empty = none).
cfgcheck() {
  local pair n f changed="" now
  for pair in $(cfg_pairs); do
    n=${pair%%:*}; f=${pair#*:}
    [ -f "$BAK/$n.sha" ] || continue
    now=$(sha "$f")
    if [ "$now" != "$(cat "$BAK/$n.sha")" ]; then
      changed="$changed $n"; : > "$OUT/cfgchanged"
      if [ -e "$OUT/foreign" ]; then :
      elif [ -f "$BAK/$n" ]; then cp -p "$BAK/$n" "$f"; else rm -f "$f"; fi
    fi
  done
  kv cfg_changed "${changed# }"
  [ -n "$changed" ] && [ -e "$OUT/foreign" ] && kv cfg_not_restored 1
  return 0
}

one() {
  local sys=$1 rom=$2 romp dry cmd bin core="" wrap ap pid rc=0 israwa=0 fail="" state=alive diedat="" b i
  romp=$COIN_DOJO_ROMS/$sys/$rom
  mkdir -p "$OUT"; rm -f "$OUT/$sys".* "$OUT/$sys-"*
  kv system "$sys"; kv rom "$rom"
  b=$(busy); if [ -n "$b" ]; then kv result FAIL; kv reason "refused: a game is already running (pid $b)"; kv busy "$b"; return 9; fi
  [ -f "$romp" ] || { kv result "$MISSING"; kv reason "ROM missing: $romp"; return 0; }
  dry=$(python3 "$LAUNCH" --dry-run "$romp" 2>&1) || { kv result FAIL; kv reason "launch.py --dry-run failed: $dry"; return 0; }
  cmd=${dry#*cmd=}
  eval "set -- $cmd"
  bin=$1
  while [ $# -gt 0 ]; do [ "$1" = "-L" ] && { core=$2; break; }; shift; done
  kv cmd "$cmd"
  [ -x "$bin" ] || { kv result "$MISSING"; kv reason "emulator missing: $bin"; return 0; }
  if [ -n "$core" ] && [ ! -f "$core" ]; then kv result "$MISSING"; kv reason "core missing: $core"; return 0; fi

  # The test must never write into the real retroarch.cfg (config_save_on_exit is true there): the
  # appendconfig's FIRST line switches saving off. COIN_DOJO_RETROARCH is launch.py's own override.
  case ${bin##*/} in
    retroarch*)
      ap=$OUT/$sys-append.cfg; wrap=$OUT/$sys-ra.sh
      mkdir -p "$OUT/saves" "$OUT/states"
      printf '%s\n' 'config_save_on_exit = "false"' 'remap_save_on_exit = "false"' 'savestate_auto_save = "false"' \
        'autosave_interval = "0"' 'history_list_enable = "false"' \
        "savefile_directory = \"$OUT/saves\"" "savestate_directory = \"$OUT/states\"" > "$ap"
      # The wrapper leaves a marker: a run without it did not go through us (see the check below).
      printf '#!/bin/sh\ntouch "%s"\nexec "%s" --appendconfig "%s" "$@"\n' "$OUT/$sys-wrapped" "$bin" "$ap" > "$wrap"; chmod +x "$wrap"
      export COIN_DOJO_RETROARCH=$wrap; israwa=1 ;;
  esac

  [ -z "${HK_READY_FILE:-}" ] || rm -f "$HK_READY_FILE"
  set -m
  python3 "$LAUNCH" "$romp" > "$OUT/$sys.log" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" >> "$OUT/jobs"
  # Whatever ends this step (a signal, ssh dropping), the job's whole group dies with it.
  # An interrupt (not a normal end) also puts ES-DE's attract mode back, once our group is dead.
  trap 'kill -9 -- "-$pid" 2>/dev/null; if [ "${INTR:-}" = 1 ]; then sleep "$SETTLE"; attract_restore; fi' EXIT
  trap 'INTR=1; exit 130' HUP TERM INT PIPE
  # Test hook (#245): with HK_READY_FILE set, the stub emulator creates that file once it has done what it does at start
  # (the timer read, a config write), and the $WAIT clock starts then, not at the launch: under a loaded test run the
  # launch.py -> wrapper -> stub chain can take seconds, and a stub killed before it got going was judged wrongly.
  # Bounded (HK_READY_POLLS x 0.05 s, default 600 = 30 s); ends early when the job dies. Never set on the cabinet or the VM.
  if [ -n "${HK_READY_FILE:-}" ]; then
    i=0
    while [ ! -e "$HK_READY_FILE" ] && alive "$pid" && [ "$i" -lt "${HK_READY_POLLS:-600}" ]; do nap 0.05; i=$((i + 1)); done
    if [ -e "$HK_READY_FILE" ]; then kv ready 1; else kv ready 0; fi
  fi
  nap "$WAIT"
  if alive "$pid"; then
    timeout 10 grim -t ppm -s 0.25 "$OUT/$sys-a.ppm" 2>/dev/null
    # the kept shot is the cabinet's native size (CRT scanlines are visible); HK_SHOT=small halves it
    if [ "${HK_SHOT:-full}" = small ]; then timeout 10 grim -t png -s 0.5 "$OUT/$sys.png" 2>/dev/null; else timeout 10 grim -t png "$OUT/$sys.png" 2>/dev/null; fi
    nap "$GAP"
    if alive "$pid"; then timeout 10 grim -t ppm -s 0.25 "$OUT/$sys-b.ppm" 2>/dev/null; else state=dead; diedat=$((WAIT + GAP)); fi
  else
    state=dead; diedat=$WAIT
  fi
  if [ "$state" = dead ]; then wait "$pid" 2>/dev/null; rc=$?; fi
  kill -9 -- "-$pid" 2>/dev/null; kill -9 "$pid" 2>/dev/null
  [ "$state" = alive ] && { wait "$pid" 2>/dev/null; rc=$?; }
  set +m
  sleep "$SETTLE"
  classify
  if [ -n "$MINE" ]; then kv leftover "$MINE"; for b in $MINE; do kill -9 "$b" 2>/dev/null; done; fi
  if [ -n "$FOREIGN" ]; then
    : > "$OUT/foreign"
    kv foreign "$FOREIGN"; kv reason "a game started during the run (pid $FOREIGN); left alone, nothing killed, no config restored"
    cfgcheck
    return 9
  fi
  if [ "$israwa" = 1 ] && [ ! -e "$OUT/$sys-wrapped" ]; then
    fail="RetroArch ran WITHOUT the config_save_on_exit wrapper: launch.py ignored COIN_DOJO_RETROARCH (system moved to RETROARCH_BY_SYSTEM?); its config may have been rewritten"
  fi
  if [ -n "$fail" ]; then kv result FAIL; kv reason "$fail"; cfgcheck; return 0; fi
  kv result RUN; kv state "$state"; kv rc "$rc"; kv died_at "$diedat"
  [ "$state" = dead ] && kv log "$(tail -n 3 "$OUT/$sys.log" 2>/dev/null | tr '\n' '|' | cut -c1-300)"
  for i in a b; do [ -s "$OUT/$sys-$i.ppm" ] && kv "shot_$i" "$OUT/$sys-$i.ppm"; done
  [ -s "$OUT/$sys.png" ] && kv png "$OUT/$sys.png"
  cfgcheck
}

finish() {
  local pair n f b keep=0 arc=0
  for pair in $(cfg_pairs); do n=${pair%%:*}; f=${pair#*:}; kv "sha_after.$n" "$(sha "$f")"; done
  classify
  if [ -n "$MINE" ]; then kv leftover "$MINE"; for b in $MINE; do kill -9 "$b" 2>/dev/null; done; fi
  if [ -n "$FOREIGN" ]; then : > "$OUT/foreign"; kv foreign "$FOREIGN"; fi
  cfgcheck
  attract_restore; arc=$?
  # Everything the controller needs has been pulled by now.
  [ -e "$OUT/cfgchanged" ] && keep=1; [ -e "$OUT/foreign" ] && keep=1; [ "$arc" = 0 ] || keep=1
  rm -rf "$OUT"; [ "$keep" = 1 ] || rm -rf "$BAK"
  if [ -n "$FOREIGN" ]; then return 9; fi
  [ -z "$MINE" ] || return 7
  [ "$arc" = 0 ] || return 1
  kv finish ok
}

main() {
  local step=${1:-}; shift
  case $step in
    guard) guard ;;
    prep) prep ;;
    one) [ $# -eq 2 ] || { echo "usage: one SYSTEM ROM" >&2; return 2; }; one "$1" "$2" ;;
    attract-off) attract_off ;;
    attract-restore) attract_restore ;;
    finish) finish ;;
    *) echo "usage: smoke_remote.sh guard|prep|attract-off|one SYSTEM ROM|attract-restore|finish" >&2; return 2 ;;
  esac
}
main "$@"
exit $?
