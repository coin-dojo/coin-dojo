#!/usr/bin/env python3
"""strip_trophies_hint.py THEME_XML: remove the main menu's LP Trophies hint (#178, #180, #181) from a staged theme.xml.

Removes the block between the trophies-hint begin/end markers (#180: the moved help bar position, the LP icon, the
label) and the old single <image name="trophiesHint"> (#177). Exit 1 when anything of the hint is left, so a theme the
patterns miss fails the staging instead of shipping a button that does nothing."""
import re
import sys

if len(sys.argv) != 2:
    sys.exit("usage: strip_trophies_hint.py THEME_XML")
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
# never across another begin marker: a block that lost its end marker must not swallow the theme up to the next one
t = re.sub(r"[ \t]*<!-- trophies-hint:begin(?:(?!trophies-hint:begin).)*?<!-- trophies-hint:end -->\n", "", t, flags=re.S)
t = re.sub(r'[ \t]*<image name="trophiesHint">.*?</image>\n', "", t, flags=re.S)
if re.search(r"trophiesHint|trophies-hint:", t):
    sys.exit("trophies hint not removed")
open(p, "w", encoding="utf-8").write(t)
