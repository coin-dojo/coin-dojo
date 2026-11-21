"""On the cabinet (read-only): resolve folder_picks.tsv to screenshot paths. For each regex, the first link in
the folder whose name matches; fill unresolved slots with the folder's top-rated games. Prints
virtual, folder, slot, screenshot, how (pick|auto|MISSING:<regex>)."""
import glob
import os
import re
import sys

# the virtual-system names come from skills/collections/nested.py (hk_virtual_systems, #94): next to the collections
# tools on the cabinet (/data/coin-dojo, with env HK_VIRTUAL_SYSTEMS) or in the checkout (the parent folder)
for _d in (os.path.dirname(os.path.abspath(__file__)), os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")):
    sys.path.append(_d)
import nested  # noqa: E402

# defaults are the cabinet's; the env vars let you run it on the laptop against a dry-run `nested --apply` tree,
# an (empty placeholder) mirror of /data/media and a copy of the gamelists (ratings for the auto fill)
NEST = os.environ.get("PICKS_NEST", "/data/coin-dojo/nested_out/roms_nested")
MEDIA = os.environ.get("PICKS_MEDIA", "/data/media")
GL = os.environ.get("PICKS_GAMELISTS", os.path.expanduser("~/ES-DE/gamelists"))


def shot(link):
    real = os.path.realpath(link)
    system, fn = real.split("/")[-2], real.split("/")[-1]
    s = sorted(glob.glob(glob.escape(os.path.join(MEDIA, system, "screenshots", os.path.splitext(fn)[0])) + ".*"))
    return (s[0] if s else None), (system, fn)


rating = {}
for d in os.listdir(GL):
    p = os.path.join(GL, d, "gamelist.xml")
    if d in nested.ALL_VIRTUAL or not os.path.exists(p):
        continue
    for g in re.findall(r"<game>(.*?)</game>", open(p, errors="replace").read(), re.S):
        pa, r = re.search(r"<path>\./(.*?)</path>", g), re.search(r"<rating>([\d.]+)</rating>", g)
        if pa:
            rating[(d, pa.group(1))] = float(r.group(1)) if r else 0.0

for line in open(sys.argv[1]):
    if not line.strip() or line.startswith("#"):
        continue
    v, folder, pats = line.rstrip("\n").split("\t")
    fdir = os.path.join(NEST, v, folder)
    if not os.path.isdir(fdir):
        print("%s\t%s\t-\t-\tNOFOLDER" % (v, folder))
        continue
    links = sorted(os.listdir(fdir))
    chosen, used = [], set()
    for pat in pats.split("|"):
        hit = next((l for l in links if re.search(pat, l, re.I) and l not in used), None)
        s = shot(os.path.join(fdir, hit))[0] if hit else None
        if hit and s:
            chosen.append((s, "pick")); used.add(hit)
        else:
            chosen.append((None, "MISSING:" + pat))
    auto = sorted(((-rating.get(shot(os.path.join(fdir, l))[1], 0.0), l) for l in links if l not in used))
    for i, (s, how) in enumerate(chosen):
        if s is None:
            while auto:
                _r, l = auto.pop(0)
                s2 = shot(os.path.join(fdir, l))[0]
                if s2:
                    chosen[i] = (s2, "auto(%s)" % how); used.add(l); break
    for i, (s, how) in enumerate(chosen):
        print("%s\t%s\t%d\t%s\t%s" % (v, folder, i, s, how))
