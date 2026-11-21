#!/usr/bin/env bash
# cabinet-status (#143): read-only health of the cabinet in one ssh call (make cabinet-status).
# Host, uptime, ES-DE up, a running game, page size + kernel, free space on / and /data, zram swap, and the deployed
# REV stamp next to this checkout's version. The remote part only reads (hostname, uptime, pgrep, getconf, uname, df,
# /proc/swaps, grep of theme.xml); it is sent on stdin to `sh -s`, so the call log of a stub ssh shows every command.
# Env (set by mk/cabinet.mk): CABINET_SSH (the ssh command, a stub in tests), CABINET (user@host, for messages),
#   GUARD_CMD (the pgrep line of cabinet-guard), ESDE_HOME, THEME, LOCAL_REV (git describe of this checkout),
#   ROOT_MIN_MB (WARN below this many MB free on /, default 300).
# Exit 0 = healthy (WARN lines allowed), 1 = unreachable or ES-DE down, 2 = usage.
set -uo pipefail
: "${CABINET_SSH:?CABINET_SSH is required}"
CABINET=${CABINET:-cabinet}
GUARD_CMD=${GUARD_CMD:-pgrep -x retroarch; pgrep -x flycast; true}
ESDE_HOME=${ESDE_HOME:-ES-DE}
THEME=${THEME:-}
LOCAL_REV=${LOCAL_REV:-unknown}
ROOT_MIN_MB=${ROOT_MIN_MB:-300}
case $ESDE_HOME$THEME in *"'"*) echo "cabinet-status: ESDE_HOME/THEME must not contain a quote" >&2; exit 2 ;; esac

remote() {
  printf "ESDE_HOME='%s'\nTHEME='%s'\n" "$ESDE_HOME" "$THEME"
  cat <<'EOF'
echo "host=$(hostname)"
echo "uptime=$(uptime)"
if pgrep -f AppRun >/dev/null 2>&1; then echo "esde=up"; else echo "esde=down"; fi
EOF
  # shellcheck disable=SC2016  # the $( ) runs on the cabinet
  printf 'echo "game=$( { %s; } 2>/dev/null | tr "\\n" " ")"\n' "$GUARD_CMD"
  cat <<'EOF'
echo "pagesize=$(getconf PAGESIZE)"
echo "kernel=$(uname -r)"
df -Pk / /data 2>/dev/null | awk 'NR > 1 { print "df=" $6 " " int($4 / 1024) }'
awk 'NR > 1 && $1 ~ /zram/ { print "zram=" $1 " size=" int($3 / 1024) "M used=" int($4 / 1024) "M" }' /proc/swaps 2>/dev/null
# #160: only the REV text element (comment lines say REV too), like keep_rev.sh
rev=$(sed -n 's|.*<text[>]REV \([^<]*\)<.*|\1|p' "$ESDE_HOME/themes/$THEME/theme.xml" 2>/dev/null | head -n 1)
echo "rev=$rev"
EOF
}

out=$(remote | $CABINET_SSH sh -s) || { echo "cabinet-status: cannot reach $CABINET" >&2; echo "RESULT: FAIL cabinet-status: unreachable"; exit 1; }
val() { printf '%s\n' "$out" | sed -n "s/^$1=//p" | head -n 1; }
rc=0; warns=0
printf '%-10s %s\n' host "$(val host)" uptime "$(val uptime | sed 's/^ *//')" kernel "$(val kernel)  (page size $(val pagesize))"
if [ "$(val esde)" = up ]; then printf '%-10s %s\n' ES-DE up; else printf '%-10s %s\n' ES-DE "DOWN (runbooks/cabinet-recovery.md)"; rc=1; fi
game=$(val game | sed 's/ *$//')
printf '%-10s %s\n' game "${game:-none running}"
printf '%s\n' "$out" | sed -n 's/^df=//p' | while read -r mnt mb; do
  note=""; if [ "$mnt" = / ] && [ "$mb" -lt "$ROOT_MIN_MB" ]; then note="  WARN under ${ROOT_MIN_MB} MB"; fi
  printf '%-10s %s MB free%s\n' "df $mnt" "$mb" "$note"
done
if printf '%s\n' "$out" | grep -q '^df=/ ' && [ "$(printf '%s\n' "$out" | sed -n 's/^df=\/ //p')" -lt "$ROOT_MIN_MB" ]; then warns=$((warns + 1)); fi
zram=$(printf '%s\n' "$out" | sed -n 's/^zram=//p' | tr '\n' ' ')
printf '%-10s %s\n' zram "${zram:-none}"
rev=$(val rev)
if [ -z "$rev" ]; then printf '%-10s %s\n' REV "(no stamp found)"
elif [ "$rev" = "$LOCAL_REV" ]; then printf '%-10s %s\n' REV "$rev (= this checkout)"
else printf '%-10s %s\n' REV "$rev  WARN this checkout is $LOCAL_REV"; warns=$((warns + 1)); fi
if [ "$rc" -ne 0 ]; then echo "RESULT: FAIL cabinet-status: ES-DE is down on $CABINET"; exit 1; fi
echo "RESULT: PASS cabinet-status ($warns warning(s))"
