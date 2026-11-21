#!/bin/sh
# keep_rev.sh <staged theme.xml> <live theme.xml> (#122)
# The staged theme.xml carries "REV <git describe>", which changes with every commit (and flips "+dirty").
# The footer must tell the truth (the maintainer 2026-11-15, #158): a new commit ships its stamp (one ES-DE restart).
# Only when the files are identical apart from the REV text AND both stamps name the same commit (a dirty
# flag flip alone: +dirty / -dirty) is the LIVE stamp put into the staged file, so rsync -c sees no
# difference and a repeat deploy stays changed=0. Prints "kept" or "new"; exits 0 unless an argument is missing.
set -eu
[ $# -eq 2 ] || { echo "usage: keep_rev.sh staged live" >&2; exit 2; }
staged=$1; live=$2
if [ ! -f "$live" ]; then echo new; exit 0; fi
norm() { sed 's|<text>REV [^<]*</text>|<text>REV X</text>|' "$1"; }
# the commit a stamp names: the REV text without a dirty flag
commit() { sed -n 's|.*<text>REV \([^<]*\)</text>.*|\1|p' "$1" | head -n 1 | sed 's/[+-]dirty$//'; }
if [ "$(norm "$staged")" = "$(norm "$live")" ] && [ "$(commit "$staged")" = "$(commit "$live")" ]; then
  cp "$live" "$staged"
  echo kept
else
  echo new
fi
