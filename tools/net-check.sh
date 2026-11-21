#!/usr/bin/env bash
# net-check (#213): read-only network quality for online play (make net-check). Pings a public host and the default
# gateway at the same time for SECONDS (default 60, one ping every 0.2 s) and prints sent, loss, avg, max and mdev for
# each. Rollback netcode hides steady latency; jitter (mdev, max) and loss are what make it stutter. Use it before and
# after a network change (Wi-Fi power save, a cable, 5 GHz) and record both runs on the ticket.
# The remote part only reads (ip route, ping -q); it is sent on stdin to `sh -s`, so a stub ssh logs every command.
# Env (set by mk/cabinet.mk): NET_CHECK_SSH (the ssh command, a stub in tests; empty = measure THIS machine),
#   CABINET (user@host, for messages), NET_CHECK_HOST (public host, hk_net_check_host), NET_CHECK_SECONDS (10..600),
#   NET_CHECK_INTERVAL (seconds between pings, default and floor 0.2), NET_WARN_LOSS (percent, default 1),
#   NET_WARN_MDEV (ms, default 10).
# Output: one row per target, WARN lines, then RESULT. Exit 0 = both targets measured (WARN allowed), 1 = unreachable,
# no gateway, 100 % loss or unreadable ping output, 2 = usage.
set -uo pipefail
SSH=${NET_CHECK_SSH:-}
CABINET=${CABINET:-cabinet}
HOST=${NET_CHECK_HOST:-}
SECONDS_RUN=${NET_CHECK_SECONDS:-60}
INTERVAL=${NET_CHECK_INTERVAL:-0.2}
WARN_LOSS=${NET_WARN_LOSS:-1}
WARN_MDEV=${NET_WARN_MDEV:-10}

usage() { echo "net-check: $1" >&2; echo "RESULT: FAIL net-check: usage"; exit 2; }
[ -n "$HOST" ] || usage "NET_CHECK_HOST is empty (config hk_net_check_host)"
case $HOST in -* | *[!A-Za-z0-9.:-]*) usage "NET_CHECK_HOST must be a host name or address (not starting with -): $HOST" ;; esac
case $SECONDS_RUN in '' | *[!0-9]*) usage "NET_CHECK_SECONDS must be a whole number: $SECONDS_RUN" ;; esac
[ "$SECONDS_RUN" -ge 10 ] && [ "$SECONDS_RUN" -le 600 ] || usage "NET_CHECK_SECONDS must be 10..600: $SECONDS_RUN"
# 0.2 s is the floor: the least a non-root ping allows, and never 0 (division) or a flood.
printf '%s\n' "$INTERVAL" | grep -Eq '^[0-9]+(\.[0-9]+)?$' || usage "NET_CHECK_INTERVAL must be a number: $INTERVAL"
awk -v i="$INTERVAL" 'BEGIN { exit !(i + 0 >= 0.2) }' || usage "NET_CHECK_INTERVAL must be at least 0.2 s: $INTERVAL"
COUNT=$(awk -v s="$SECONDS_RUN" -v i="$INTERVAL" 'BEGIN { c = int(s / i); print (c < 1 ? 1 : c) }')

# The part that runs where we measure. Linux: ip route; macOS (a local run on the Mac): route -n get default.
remote() {
  printf "HOST='%s'\nCOUNT='%s'\nINTERVAL='%s'\n" "$HOST" "$COUNT" "$INTERVAL"
  cat <<'EOF'
gw=$(ip route show default 2>/dev/null | awk '$1 == "default" { print $3; exit }')
[ -n "$gw" ] || gw=$(route -n get default 2>/dev/null | awk '$1 == "gateway:" { print $2; exit }')
echo "gw=$gw"
echo "iface=$(ip route show default 2>/dev/null | awk '$1 == "default" { for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }')"
ping -q -n -i "$INTERVAL" -c "$COUNT" "$HOST" 2>&1 | sed 's/^/public: /' &
if [ -n "$gw" ]; then ping -q -n -i "$INTERVAL" -c "$COUNT" "$gw" 2>&1 | sed 's/^/gateway: /' & fi
wait
EOF
}

if [ -n "$SSH" ]; then where=$CABINET; out=$(remote | $SSH sh -s) || [ -n "${out:-}" ] || {
  echo "net-check: cannot reach $CABINET" >&2; echo "RESULT: FAIL net-check: $CABINET unreachable"; exit 1; }
else where="this machine"; out=$(remote | sh -s); fi

gw=$(printf '%s\n' "$out" | sed -n 's/^gw=//p' | head -n 1)
iface=$(printf '%s\n' "$out" | sed -n 's/^iface=//p' | head -n 1)
echo "net-check on $where${iface:+ ($iface)}: ${SECONDS_RUN} s, $COUNT pings each"
printf '%-8s %-16s %5s %7s %8s %8s %8s\n' target host sent loss avg_ms max_ms mdev_ms
fails=0; warns=0
row() {   # row <prefix> <host>: parse that target's ping summary (Linux "rtt ... mdev", macOS "round-trip ... stddev")
  local lines sent loss rtt avg max mdev
  lines=$(printf '%s\n' "$out" | sed -n "s/^$1: //p")
  sent=$(printf '%s\n' "$lines" | sed -n 's/^\([0-9][0-9]*\) packets transmitted.*/\1/p' | head -n 1)
  loss=$(printf '%s\n' "$lines" | sed -n 's/.* \([0-9.][0-9.]*\)% packet loss.*/\1/p' | head -n 1)
  rtt=$(printf '%s\n' "$lines" | sed -n 's|.* = \([0-9.]*\)/\([0-9.]*\)/\([0-9.]*\)/\([0-9.]*\) ms.*|\2 \3 \4|p' | head -n 1)
  if [ -z "$sent" ] || [ -z "$loss" ]; then
    printf '%-8s %-16s %s\n' "$1" "$2" "no ping summary"
    echo "FAIL $1 $2: no ping summary: $(printf '%s' "$lines" | tr '\n' ' ' | cut -c1-200)"; fails=$((fails + 1)); return
  fi
  if [ -z "$rtt" ]; then
    printf '%-8s %-16s %5s %6s%% %8s %8s %8s\n' "$1" "$2" "$sent" "$loss" - - -
    echo "FAIL $1 $2: no reply ($loss% loss)"; fails=$((fails + 1)); return
  fi
  read -r avg max mdev <<<"$rtt"
  printf '%-8s %-16s %5s %6s%% %8s %8s %8s\n' "$1" "$2" "$sent" "$loss" "$avg" "$max" "$mdev"
  if awk -v l="$loss" -v w="$WARN_LOSS" 'BEGIN { exit !(l + 0 > w + 0) }'; then
    echo "WARN $1 $2: $loss% loss (over $WARN_LOSS%)"; warns=$((warns + 1)); fi
  if awk -v m="$mdev" -v w="$WARN_MDEV" 'BEGIN { exit !(m + 0 > w + 0) }'; then
    echo "WARN $1 $2: jitter mdev $mdev ms (over $WARN_MDEV ms)"; warns=$((warns + 1)); fi
}
row public "$HOST"
if [ -n "$gw" ]; then row gateway "$gw"; else echo "FAIL gateway: no default route on $where"; fails=$((fails + 1)); fi
if [ "$fails" -ne 0 ]; then echo "RESULT: FAIL net-check ($fails target(s) not measured on $where)"; exit 1; fi
echo "RESULT: PASS net-check ($warns warning(s))"
