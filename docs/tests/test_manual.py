#!/usr/bin/env python3
"""The retro instruction booklet (#272): docs/manual/coin-dojo-manual.html, built by docs/assets/gen_manual.py.

  1. one content source: every numbered step of docs/START.md is in the booklet word for word (plain text), read
     through gen_manual.paper() (links to other guides become page references, the release placeholder the Releases page);
  2. the committed HTML equals a fresh build byte for byte (stdlib, no Pillow needed; in the community tree, which
     has no denylist for gen_manual.export_guard, up to its line breaks);
  3. it is self-contained and small: under gen_manual.MAX_BYTES, every src/href is a data: URI or a #fragment;
  4. 16 pages (a multiple of 4: it folds into a booklet), each with its PNG in docs/manual/pages/ (same size);
  5. the cover and back page carry the name, the Iconic Arcade tagline and the not-affiliated line;
  6. README.md links START.md ("Start here") and the booklet ("retro instruction booklet");
  7. paper words (Everyday Owner review #282): no "TBD", no repo file name (ROMS.md, skills/...), BIOS explained;
  8. the SETTINGS page lists the app's home in its order (settings_core.HOME_ORDER) and the pages it covers say their
     choices (GAME LISTS sizes and stars, ATTRACT MODE minutes and shows, PASSWORD 12345 typed twice, SHOW FPS),
     plus power-off, the navigation shortcuts, the Fightcade guide and scraping.
Each rule has a planted broken case that must FAIL. Plain python (hktest), stdlib only.
"""
import html
import os
import re
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(ROOT, "docs", "assets"))
from hktest import check, main  # noqa: E402
import gen_manual as gm  # noqa: E402

PAGES = 16


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def text_of(doc):
    """The booklet's visible words: no style/script, no tags, entities decoded, whitespace collapsed."""
    doc = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", doc, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", doc)).split())


def norm(s):
    return re.sub(r"\(\s+", "(", re.sub(r"\s+([.,:;)])", r"\1", " ".join(s.split())))


def step_problems(steps, doc):
    """Each step's title and every line of its text must appear in the booklet (bullets one by one)."""
    words, probs = norm(text_of(doc)), []
    for st in steps:
        if st["title"].upper() not in words:
            probs.append("step %d title %r missing" % (st["n"], st["title"]))
        for ln in st["lines"]:
            p = norm(gm.plain(gm.paper(gm.SUB_RX.sub("", ln))))
            for chunk in [c for c in re.split(r"(?<=[.:])\s+", p) if c]:
                if chunk not in words:
                    probs.append("step %d: %r not in the booklet" % (st["n"], chunk[:60]))
    return probs


def link_problems(doc):
    return ["external %s=%r" % (a, v[:40]) for a, v in re.findall(r'\b(src|href)="([^"]*)"', doc)
            if not (v.startswith("data:") or v.startswith("#"))] + \
           ["external url() %r" % u[:40] for u in re.findall(r"url\(([^)]*)\)", doc) if not u.startswith("data:")]


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def pages_problems(doc, pages_dir):
    probs = []
    n = len(re.findall(r'<section class="page[^"]*" id="p\d+"', doc))
    if n != PAGES or n % 4:
        probs.append("%d pages, want %d (a multiple of 4)" % (n, PAGES))
    sizes = set()
    for i, name in enumerate(gm.PAGE_NAMES, 1):
        p = os.path.join(pages_dir, "%02d-%s.png" % (i, name))
        s = png_size(p) if os.path.isfile(p) else None
        if s is None:
            probs.append("no page PNG %s" % os.path.basename(p))
        else:
            sizes.add(s)
    if len(sizes) > 1:
        probs.append("page PNGs differ in size: %s" % sorted(sizes))
    return probs


def test_steps_come_from_start():
    steps, doc = gm.parse_start(), read(gm.OUT)
    check(6 <= len(steps) <= 9, "START.md has 6 to 9 numbered steps (%d)" % len(steps))
    check(step_problems(steps, doc) == [], "every START.md step is in the booklet word for word %s" %
          step_problems(steps, doc)[:3])
    drifted = [dict(st) for st in steps]
    drifted[3] = dict(drifted[3], lines=drifted[3]["lines"] + ["Log in as admin with password hunter2."])
    check(step_problems(drifted, doc) != [], "BROKEN: a START.md sentence the booklet lacks is reported")


def test_rebuild_is_byte_identical():
    with tempfile.TemporaryDirectory() as tmp:
        fresh, n = gm.build(os.path.join(tmp, "m.html"))
        guarded = os.path.isfile(os.path.join(ROOT, "config", "edition", "denylist.txt"))
        same = read(fresh) == read(gm.OUT) if guarded else \
            read(fresh).replace("\n", "") == read(gm.OUT).replace("\n", "")   # community tree: no export guard
        check(same and n == PAGES, "the committed booklet equals a fresh build (else: python3 docs/assets/gen_manual.py)")
        with open(fresh, "a", encoding="utf-8") as f:
            f.write("<!--drift-->")
        check(read(fresh) != read(gm.OUT), "BROKEN: a changed byte is reported as drift")


def test_export_guard():
    deny = os.path.join(ROOT, "config", "edition", "denylist.txt")
    if not os.path.isfile(deny):
        return                                                       # community tree: nothing to guard
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import edition_export as ee
    pats = [rx for _l, rx in ee.load_denylist(deny)]
    word = [ln.split("#")[0].strip() for ln in open(deny, encoding="utf-8") if ln.split("#")[0].strip()][0]
    planted = '<img src="data:image/png;base64,AA1%s2BB">' % word.replace(" ", "")
    check(any(rx.search(planted) for rx in pats), "premise: the planted base64 run reads as a denylisted word")
    check(not any(rx.search(gm.export_guard(planted)) for rx in pats), "BROKEN: export_guard breaks such a run")
    doc = read(gm.OUT)
    spans = [(m.start(), m.end()) for m in re.finditer(r"data:[^)\"']+", doc)]
    hits = [m.group(0) for rx in pats for m in rx.finditer(doc) if any(a <= m.start() < b for a, b in spans)]
    check(hits == [], "no denylisted word reads in the embedded data (%d)" % len(hits))


def test_self_contained_and_small():
    doc, size = read(gm.OUT), os.path.getsize(gm.OUT)
    check(size <= gm.MAX_BYTES, "booklet is %d bytes <= %d" % (size, gm.MAX_BYTES))
    check(link_problems(doc) == [], "every src/href/url() is embedded %s" % link_problems(doc)[:3])
    check(link_problems(doc + '<img src="https://example.invalid/x.png">') != [],
          "BROKEN: an external image is reported")


def test_pages_and_pngs():
    doc = read(gm.OUT)
    check(pages_problems(doc, gm.PAGES_DIR) == [], "%d pages, each with its PNG %s" % (PAGES, pages_problems(doc, gm.PAGES_DIR)))
    with tempfile.TemporaryDirectory() as tmp:
        check(any("no page PNG" in p for p in pages_problems(doc, tmp)), "BROKEN: missing page PNGs are reported")
    cut = doc.replace('<section class="page back" id="p16">', '<div id="p16">', 1)
    check(pages_problems(cut, gm.PAGES_DIR) != [], "BROKEN: a booklet with 15 pages is reported")


def test_name_tagline_iconic():
    words = text_of(read(gm.OUT))
    for want in (gm.NAME, gm.TAGLINE, gm.ICONIC, "INSTRUCTION BOOKLET", "WARNING: READ BEFORE USING"):
        check(want in words, "the booklet says %r" % want)
    check(words.count(gm.TAGLINE) >= 2, "the tagline is on the cover and the back page")


def test_readme_links():
    readme = read(os.path.join(ROOT, "README.md"))
    line = " ".join(readme.split("\n\n")[[i for i, p in enumerate(readme.split("\n\n")) if "New here?" in p][0]].split())
    check("](docs/START.md)" in line and "retro instruction booklet" in line and "](docs/manual/README.md)" in line,
          "README's 'New here?' paragraph links START.md and the booklet page")
    check("coin-dojo-manual.html)" not in line, "README links the booklet page, not the .html (GitHub shows .html as source)")


def booklet_md_problems(text, page_files):
    want = ["pages/%s" % f for f in sorted(page_files)]
    got = re.findall(r"!\[[^\]]+\]\((pages/[^)]+)\)", text)
    probs = [] if got == want else ["the booklet page shows %s, the pages folder has %s" % (got, want)]
    if "](coin-dojo-manual.html)" not in text:
        probs.append("the booklet page does not link the printable .html")
    return probs


def test_booklet_md():
    text = read(gm.BOOKLET_MD)
    files = [f for f in os.listdir(gm.PAGES_DIR) if f.endswith(".png")]
    check(booklet_md_problems(text, files) == [], "docs/manual/README.md shows every page in order %s"
          % booklet_md_problems(text, files))
    check(text == gm.booklet_md(), "docs/manual/README.md is what gen_manual.py writes (run it)")
    check(booklet_md_problems(text, files + ["17-extra.png"]) != [], "BROKEN: a page missing from the booklet page is reported")
    check(booklet_md_problems(text.replace("](coin-dojo-manual.html)", "](x.html)"), files) != [],
          "BROKEN: no print link is reported")


PAPER_RX = [("TBD", re.compile(r"\bTBD\b")), ("a .md file name", re.compile(r"\b[\w-]+\.md\b")),
            ("a repo path", re.compile(r"\b(skills|docs|ansible|tools|config)/[\w./-]+"))]


def paper_problems(words):
    return ["%s: %r" % (what, m.group(0)) for what, rx in PAPER_RX for m in rx.finditer(words)]


def test_paper_words():
    words = text_of(read(gm.OUT))
    check(paper_problems(words) == [], "no TBD, file name or repo path on paper %s" % paper_problems(words)[:3])
    for bad in ("download: TBD (#267)", "Folder names: ROMS.md.", "see skills/collections/SKILL.md"):
        check(paper_problems(words + " " + bad) != [], "BROKEN: %r is reported" % bad)
    check(gm.paper("from the release page: TBD (#267).") == "from %s." % gm.RELEASES, "paper() names the Releases page")
    check(gm.paper("Folder names: [ROMS.md](ROMS.md).") == "Folder names: the folder table on page 5.",
          "paper() turns a guide link into a page reference")
    check("project's page" in gm.paper("[X](NEW.md)"), "an unknown guide link becomes the project's page")
    for want in ("WHAT IS A BIOS?", "small start-up file", "dumped", "the project's Releases page"):
        check(want.lower() in words.lower(), "the booklet says %r" % want)
    check(gm.paper("from the project's [Releases page][releases].") == "from the project's Releases page.",
          "paper() prints a reference-style link as its text")
    sub = gm.step_html(dict(n=2, title="Write it", lines=["with Imager:", "1. Pick **Pi 5**.", "2. Click **Write**."]))
    check("<ol><li>Pick <b>Pi 5</b>.</li><li>Click <b>Write</b>.</li></ol>" in sub, "sub-steps become a numbered list")
    flat = gm.step_html(dict(n=2, title="Write it", lines=["with Imager: 1. Pick Pi 5. 2. Click Write."]))
    check("<ol>" not in flat, "BROKEN: sub-steps inside a sentence are not a list")


def home_order():
    src = read(os.path.join(ROOT, "skills", "settings", "settings_core.py"))
    m = re.search(r"^HOME_ORDER = \(([^)]*)\)", src, re.M)
    return re.findall(r'"(\w+)"', m.group(1)) if m else []


def order_problems(order_ids, listed):
    want = [gm.SETTINGS_IDS.get(i, "?" + i) for i in order_ids] + ["BACK"]
    return [] if want == listed else ["want %s, booklet %s" % (want, listed)]


def test_settings_order_and_pages():
    listed = [t for t, _v in gm.SETTINGS_ORDER]
    check(order_problems(home_order(), listed) == [], "SETTINGS order matches HOME_ORDER %s"
          % order_problems(home_order(), listed))
    check(listed == ["WI-FI", "CABINET NAME", "PASSWORD", "GAME LISTS", "ATTRACT MODE", "SHOW FPS", "RETROACHIEVEMENTS",
                     "TIME ZONE", "BACK"], "the agreed SETTINGS order")
    swapped = home_order()[:]
    swapped[2], swapped[3] = swapped[3], swapped[2]
    check(order_problems(swapped, listed) != [], "BROKEN: the app's order changing is reported")
    check(order_problems(home_order() + ["newpage"], listed) != [], "BROKEN: a new SETTINGS page is reported")
    words = text_of(read(gm.OUT))
    for want in ("REBUILD GAME LISTS", "MUST PLAY SIZE", "10, 15, 25 or 50", "MUST PLAY GENRES MIN STARS",
                 "4.0, 4.5 or 5.0", "ATTRACT MODE", "5, 10, 30 or 60 minutes", "VIDEO", "SLIDESHOW", "DIM", "BLACK",
                 "APPLY NOW", "SHOW FPS", "password 12345", "a second time", "power button", "NO SIGNAL",
                 "Jump 10 games", "jump to a letter", "www.fightcade.com", "Online play (Fightcade)", "SCRAPER",
                 "screenscraper.fr"):
        check(want in words, "the booklet says %r" % want)


if __name__ == "__main__":
    main(globals())
