#!/bin/sh
# cache-migrate (#142): move the old per-area cache folders under the one cache root HK_CACHE.
#   make cache-migrate [APPLY=1]       (dry run unless APPLY=1)
# ~/.cache/coin-dojo-<name> -> $HK_CACHE/<name> for name in ansible preview vm ws. A folder whose new place already
# exists is left alone (merge it by hand); nothing is ever deleted. Stop running agents first: a moved ws/ folder
# breaks a worktree or workspace that is in use (run `git worktree prune` afterwards).
# Exit 0 = done (or nothing to do), 1 = a move failed, 2 = usage.
set -eu
HK_CACHE=${HK_CACHE:-$HOME/.cache/coin-dojo}
APPLY=${APPLY:-0}
case $APPLY in 0|1) ;; *) echo "cache-migrate: APPLY must be 0 or 1 (got '$APPLY')" >&2; exit 2 ;; esac
case $HK_CACHE in "$HOME"/*) ;; *) echo "cache-migrate: HK_CACHE=$HK_CACHE must be under \$HOME (Colima shares only \$HOME)" >&2; exit 2 ;; esac

todo=0; skipped=0; rc=0
for name in ansible preview vm ws; do
  old=$HOME/.cache/coin-dojo-$name
  new=$HK_CACHE/$name
  [ -d "$old" ] || continue
  if [ -e "$new" ]; then
    echo "cache-migrate: SKIP $old: $new already exists (merge it by hand)"; skipped=$((skipped + 1)); continue
  fi
  todo=$((todo + 1))
  if [ "$APPLY" = 1 ]; then
    mkdir -p "$HK_CACHE"
    if mv "$old" "$new"; then echo "cache-migrate: moved $old -> $new"; else echo "cache-migrate: FAILED to move $old" >&2; rc=1; fi
  else
    echo "DRY RUN cache-migrate: would move $old -> $new"
  fi
done
if [ "$todo" -eq 0 ]; then echo "cache-migrate: nothing to move ($skipped skipped); cache root is $HK_CACHE"
elif [ "$APPLY" != 1 ]; then echo "cache-migrate: $todo folder(s) to move; APPLY=1 to move them"; fi
exit "$rc"
