#!/usr/bin/env python3
"""Build the --folder-art dir for `collgen.py nested` from the theme's original art (stdlib only).

<out>/<virtual>/<folder>.png for every folder of the nested main screen:
  genres/<g>.png   <- <theme>/_inc/icons/<g>.png (geometric pictogram), else _inc/systems/<g>.png (badge)
  decades/<d>.png  <- <theme>/_inc/systems/<d>.png (chrome badge)
  systems/<s>.png  <- <theme>/_inc/systems/<s>.png (chrome badge)
Folders come from folder_text.tsv (one row per folder). Plan by default; --apply copies.
A folder with no art is reported (exit 3 with --strict). Exit codes: 0 ok, 2 usage, 3 missing art (--strict).

  python3 folder_art.py --theme themes/coin-dojo --text skills/collections/folder_text.tsv --out DIR [--apply] [--strict]
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nested  # noqa: E402


def sources(theme, virtual, folder):
    """Candidate art files for one folder, best first."""
    inc = os.path.join(theme, "_inc")
    cands = []
    if virtual in nested.GENRE_VIRTUALS:
        cands.append(os.path.join(inc, "icons", folder + ".png"))
    cands.append(os.path.join(inc, "systems", folder + ".png"))
    return cands


def plan(theme, keys):
    """-> ({dest_rel: src}, [missing 'virtual/folder'])"""
    out, missing = {}, []
    for v, folder in sorted(keys):
        src = next((c for c in sources(theme, v, folder) if os.path.isfile(c)), None)
        if src:
            out[os.path.join(v, folder + ".png")] = src
        else:
            missing.append("%s/%s" % (v, folder))
    return out, missing


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--theme", required=True)
    ap.add_argument("--text", required=True, help="folder_text.tsv (the folder list)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 3 if any folder has no art")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.theme):
        print("ERROR: theme dir not found: %s" % a.theme, file=sys.stderr)
        return 2
    try:
        keys = nested.load_folder_text(a.text).keys()
    except (OSError, ValueError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2
    files, missing = plan(a.theme, keys)
    for rel, src in sorted(files.items()):
        dest = os.path.join(a.out, rel)
        same = os.path.isfile(dest) and open(dest, "rb").read() == open(src, "rb").read()
        print("  %-5s %s <- %s" % ("SAME" if same else "COPY", rel, os.path.relpath(src, a.theme)))
        if a.apply and not same:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest)
    for m in missing:
        print("  MISSING %s (no icon or badge in the theme)" % m)
    print("%s: %d folder(s) with art, %d missing" % ("applied" if a.apply else "plan only", len(files), len(missing)))
    return 3 if (missing and a.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
