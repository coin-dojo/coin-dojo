#!/usr/bin/env bash
# tools/commit.sh (`make commit`, #99): the gum flow of charmbracelet/gum examples/commit.sh, extended with the
# ticket number. Builds `type(scope): #N summary` + description, then `git commit -m SUMMARY -m DESCRIPTION`
# (the commit-msg hook still validates). Needs gum: brew install gum.
set -euo pipefail
TYPES="feat fix perf refactor style docs test build ci chore revert"
die() { echo "commit: $*" >&2; exit 1; }
if git diff --cached --quiet; then die "nothing staged (git add the files first)"; fi
command -v gum >/dev/null 2>&1 || die "gum is not installed: brew install gum"
# shellcheck disable=SC2086
TYPE=$(gum choose $TYPES) || die "aborted"
[ -n "$TYPE" ] || die "no type chosen"
SCOPE=$(gum input --placeholder "scope") || die "aborted"
TICKET=$(gum input --placeholder "ticket number") || die "aborted"
TICKET=${TICKET#\#}
case "$TICKET" in
  none) PREFIX="" ;;
  ""|*[!0-9]*) die "ticket number must be digits (or 'none')" ;;
  *) PREFIX="#$TICKET " ;;
esac
HEAD="$TYPE"
[ -z "$SCOPE" ] || HEAD="$TYPE($SCOPE)"
SUMMARY=$(gum input --value "$HEAD: $PREFIX" --placeholder "Summary of this change") || die "aborted"
DESCRIPTION=$(gum write --placeholder "Details of this change") || die "aborted"
if [ "$TICKET" = none ]; then
  DESCRIPTION="${DESCRIPTION:+$DESCRIPTION

}Refs: none"
fi
gum confirm "Commit changes?" || die "aborted"
if [ -n "$DESCRIPTION" ]; then
  git commit -m "$SUMMARY" -m "$DESCRIPTION"
else
  git commit -m "$SUMMARY"
fi
