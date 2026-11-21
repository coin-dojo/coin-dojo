#!/usr/bin/env python3
"""Customer docs gate (#238): the README and docs/*.md guides keep their promises.

tests/test_docs.py already checks every doc's links, `make X` names and hk_* names. This file checks what is
special to the customer doc set:
  1. the set is complete (README, START, BUILD, ROMS, TUNING, REFERENCE, RESOURCES, FAQ,
     FIGHTCADE, SCRAPING) and the README links each guide;
  2. the warning sits at the top of README.md and docs/BUILD.md, the AI note at the top of README.md: each a banner
     whose alt text is the full text, then the same text in plain markdown right under it. One source for the words:
     TEXT in docs/assets/gen_docs_art.py;
  3. docs/REFERENCE.md names every target `make help` lists (the private-repo-only export targets excepted);
  4. docs/ROMS.md lists exactly the systems the launcher knows (CORE_BY_SYSTEM in skills/collections/launch.py);
  5. every row of TUNING.md's "at a glance" table names its source (#N, or says "no ticket");
  6. open gaps are marked "TBD (#N)" (only the Ko-fi placeholder may differ);
  7. the SVG badges equal a fresh render (stdlib); the PNG banners re-render at the same size (Pillow, else SKIP);
  8. the README's "Fork it, make it yours" section ends with the "Buy me a coffee" line, names a repo issue as the
     contact, and the README has no Ko-fi or other tip link (#284, the maintainer 2026-11-20);
  9. every BIOS md5 in ROMS.md is 32 lowercase hex digits;
 10. docs/BUILD.md and README.md keep no TBD (#244), and BUILD's image table names Fightcade only with
     hk_image_fightcade (it is opt-in, #244);
 11. README.md and docs/BUILD.md open with the platform note: tested on macOS, Linux not tested yet, Windows
     later (#248), and nothing else claimed as tested;
 12. the base image file name in BUILD.md's beginner steps equals the pin in base-image.sh (#248).
Each rule has a planted broken case that must FAIL. Plain python (hktest).
"""
import ast
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(ROOT, "docs", "assets"))
from hktest import check, main, skip  # noqa: E402
import gen_docs_art as art  # noqa: E402

GUIDES = ["docs/START.md", "docs/BUILD.md", "docs/ROMS.md", "docs/TUNING.md", "docs/REFERENCE.md", "docs/RESOURCES.md", "docs/FAQ.md", "docs/FIGHTCADE.md",
          "docs/SCRAPING.md"]
CUSTOMER_DOCS = ["README.md"] + GUIDES
TOP_LINES = 25  # "impossible to miss": within the first screen
PRIVATE_ONLY_TARGETS = {"edition-export", "edition-test"}  # mk/edition-export.mk is not exported (edits.tsv)
# Allowed open-gap forms: `TBD (#N)`; the Ko-fi placeholder `<TBD>` and its `<!-- TBD: maintainer adds ... -->`.
TBD = re.compile(r"TBD(?! \(#\d+\))(?!>)(?!: maintainer adds )")
IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def read(rel, root=ROOT):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


# ---- rule 1: the set
def set_problems(exists, readme):
    probs = ["missing %s" % p for p in CUSTOMER_DOCS if not exists(p)]
    probs += ["README.md does not link %s" % g for g in GUIDES if "](%s)" % g not in readme]
    return probs


# ---- rule 2: banner + plain copy at the top
def plain(s):
    s = re.sub(r"^\s*>\s?", "", s, flags=re.M)
    s = s.replace("**", "")
    return " ".join(s.split())


def banner_problems(rel, text, label, body, top=TOP_LINES):
    """The banner image with alt "<label>: <body>" in the first `top` lines, then <body> as plain text under it."""
    lines = text.splitlines()
    want_alt = "%s: %s" % (label, body)
    for n, line in enumerate(lines[:top]):
        m = IMG.search(line)
        if m and m.group(1).startswith(label + ":"):
            if m.group(1) != want_alt:
                return ["%s:%d: %s banner alt text differs from gen_docs_art.TEXT" % (rel, n + 1, label)]
            after = plain("\n".join(lines[n + 1:n + 8]))
            if body not in after:
                return ["%s:%d: no plain-text copy of the %s right under its banner" % (rel, n + 1, label)]
            return []
    return ["%s: no %s banner in the first %d lines" % (rel, label, top)]


# ---- rule 3: make targets
def help_targets(root=ROOT):
    r = subprocess.run(["make", "-s", "-C", root, "help"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    return set(re.findall(r"^  ([a-z][a-z0-9_-]*)\s", r.stdout, re.M))


def target_problems(ref_text, targets):
    named = set(re.findall(r"`make ([a-z][a-z0-9_-]*)", ref_text))
    return ["docs/REFERENCE.md does not list `make %s` (make help has it)" % t
            for t in sorted(targets - named - PRIVATE_ONLY_TARGETS)]


# ---- rule 4: systems
def launcher_systems(root=ROOT):
    tree = ast.parse(read("skills/collections/launch.py", root))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CORE_BY_SYSTEM" for t in node.targets):
            return set(ast.literal_eval(node.value).keys())
    return set()


def roms_systems(roms_text):
    m = re.search(r"^## Systems and folders\n(.*?)(?=^## )", roms_text, re.S | re.M)
    body = m.group(1) if m else ""
    return set(re.findall(r"^\| `([a-z0-9]+)` \|", body, re.M))


def system_problems(doc, code):
    return (["ROMS.md lists %s, the launcher does not know it" % s for s in sorted(doc - code)] +
            ["ROMS.md is missing system %s (launch.py CORE_BY_SYSTEM)" % s for s in sorted(code - doc)])


# ---- rule 5: sources in TUNING's table
def tuning_problems(text):
    m = re.search(r"^## The decisions at a glance\n(.*?)(?=^## )", text, re.S | re.M)
    if not m:
        return ["TUNING.md has no 'The decisions at a glance' section"]
    rows = [r for r in m.group(1).splitlines() if r.startswith("| ") and not r.startswith("| Decision")]
    if not rows:
        return ["TUNING.md's at-a-glance table is empty"]
    return ["TUNING.md row without a source (#N or 'no ticket'): %s" % r[:60] for r in rows
            if not re.search(r"#\d+|no ticket", r.rsplit("|", 2)[-2])]


# ---- rule 6: TBD markers
def tbd_problems(rel, text):
    return ["%s:%d: open gap not marked `TBD (#N)`" % (rel, n) for n, line in enumerate(text.splitlines(), 1)
            if TBD.search(line)]


def tbd_markers(rel, text):
    return [(rel, n, t) for n, line in enumerate(text.splitlines(), 1) for t in re.findall(r"TBD \(#\d+\)", line)]


# ---- rule 7: generated art
def badge_drift(committed_dir, fresh_dir):
    out = []
    for name in sorted(art.BADGES):
        a, b = os.path.join(committed_dir, name), os.path.join(fresh_dir, name)
        if not os.path.exists(a):
            out.append("%s missing: run docs/assets/gen_docs_art.py" % name)
        elif open(a, "rb").read() != open(b, "rb").read():
            out.append("%s differs from a fresh render: run docs/assets/gen_docs_art.py" % name)
    return out


# ---- rule 8: fork + coffee (#284, the maintainer 2026-11-20: coffee wording, no tip link, contact = a repo issue)
TIP_LINK = re.compile(r"ko-?fi|buymeacoffee\.com|patreon\.com|paypal\.me|liberapay\.com|opencollective\.com|"
                      r"github\.com/sponsors|https?://", re.I)
TIP_SITE = re.compile(r"ko-?fi\.com|buymeacoffee\.com|patreon\.com|paypal\.me|liberapay\.com|opencollective\.com|"
                      r"github\.com/sponsors", re.I)


def support_problems(readme):
    m = re.search(r"^## Fork it, make it yours\n(.*?)(?=^## )", readme, re.S | re.M)
    if not m:
        return ["README.md has no 'Fork it, make it yours' section"]
    lines = [l for l in m.group(1).splitlines() if l.strip() and not l.strip().startswith("<!--")]
    probs = []
    if not lines or "Buy me a coffee" not in lines[-1]:
        probs.append("the section must end with the 'Buy me a coffee' line: %r" % (lines[-1:] or ""))
    if not any("issue" in l for l in lines):
        probs.append("the section must name a repo issue as the contact")
    # The one tip link allowed is the maintainer's Ko-fi page from .github/FUNDING.yml (repo README only, never the
    # image or the release page: COMPLIANCE.md "Tip links"), on the coffee line and the badge row, nowhere else.
    page = kofi_page()
    probs += ["a tip link in the section outside the coffee line: %r" % l.strip()[:60]
              for l in lines[:-1] if TIP_LINK.search(l)]
    for url in re.findall(r"https?://(?:www\.)?ko-?fi\.com/[\w-]+", readme, re.I):
        if url != page:
            probs.append("a Ko-fi link that is not the FUNDING.yml page (%s): %s" % (page, url))
    other = re.sub(r"https?://(?:www\.)?ko-?fi\.com/[\w-]+", "", readme, flags=re.I)
    if TIP_SITE.search(other):
        probs.append("README.md has a tip link that is not the Ko-fi page: %r" % TIP_SITE.search(other).group(0))
    return probs


def kofi_page():
    m = re.search(r"^ko_fi:\s*([\w-]+)\s*$", read(".github/FUNDING.yml"), re.M)
    return "https://ko-fi.com/%s" % m.group(1) if m else None


# ---- rule 9: md5s
def md5_problems(roms_text):
    m = re.search(r"^## BIOS files\n(.*?)(?=^## )", roms_text, re.S | re.M)
    body = m.group(1) if m else ""
    rows = [r for r in body.splitlines() if r.startswith("| `")]
    out = []
    for r in rows:
        last = r.rstrip(" |").rsplit("|", 1)[-1].strip()
        if last.startswith("`") and not re.fullmatch(r"`[0-9a-f]{32}`", last):
            out.append("bad md5 cell: %s" % last)
    if not rows:
        out.append("ROMS.md has no BIOS rows")
    return out


# ---- rule 10: BUILD says what the image gives (#244: the image runs the cabinet's roles; Fightcade is opt-in)
FIGHTCADE = re.compile(r"fightcade", re.I)


def image_claim_problems(text):
    """#244 is done: no `TBD (#244)` may remain. In "What the image gives today", a paragraph or table row that names
    Fightcade must say it needs `hk_image_fightcade` (off by default: a default image's boot menu shows ARCADE only)."""
    probs, heading = [], ""
    if "TBD (#244)" in text:
        probs.append("BUILD.md: a TBD (#244) marker is left (the image runs the roles since #244)")
    for block in re.split(r"\n\s*\n", text):
        first = block.lstrip().splitlines()[0] if block.strip() else ""
        if first.startswith("#"):
            heading = first
            block = "\n".join(block.lstrip().splitlines()[1:])
        if heading.lower() != "## what the image gives today":
            continue
        units = block.splitlines() if block.lstrip().startswith("|") else [block]
        for u in units:
            if FIGHTCADE.search(u) and "hk_image_fightcade" not in u:
                probs.append("BUILD.md: names Fightcade in the image without hk_image_fightcade: %s" % " ".join(u.split())[:70])
    return probs


# ---- rule 11: platform note (#248: the maintainer, 2026-11-19 "note these directions work for osx so far")
PLATFORM_TOP = 30  # under the banners, still on the first screen


def platform_problems(rel, text, top=PLATFORM_TOP):
    """A '> **Platform: tested on macOS ...' quote in the first `top` lines that also names Linux as not tested
    yet and Windows as later. Nothing but macOS may be called tested."""
    lines = text.splitlines()
    for n, line in enumerate(lines[:top]):
        if line.startswith("> **Platform:"):
            note = plain("\n".join(l for l in lines[n:n + 4] if l.startswith(">")))
            probs = []
            if not re.match(r"Platform: tested on macOS\b", note):
                probs.append("%s:%d: the platform note must start 'Platform: tested on macOS'" % (rel, n + 1))
            if not re.search(r"Linux[^.]*not tested yet", note):
                probs.append("%s:%d: the platform note must say Linux is not tested yet" % (rel, n + 1))
            if "Windows" not in note:
                probs.append("%s:%d: the platform note must name Windows (coming later)" % (rel, n + 1))
            if re.search(r"(?<!not )tested on (?!macOS)", note, re.I):
                probs.append("%s:%d: the platform note calls another platform tested" % (rel, n + 1))
            return probs
    return ["%s: no '> **Platform: tested on macOS' note in the first %d lines" % (rel, top)]


# ---- rule 12: the beginner steps' base image name follows the pin
def pinned_base_name(root=ROOT):
    m = re.search(r"^PIN_URL=\S*/([^/\s]+)\.xz$", read("skills/build-image/scripts/base-image.sh", root), re.M)
    return m.group(1) if m else None


def base_name_problems(build_text, pinned):
    if not pinned:
        return ["base-image.sh: no PIN_URL=...img.xz line"]
    names = set(re.findall(r"base-image/([\w.-]+\.img)\b", build_text))
    if not names:
        return ["BUILD.md: the beginner steps name no base-image/<file>.img"]
    return ["BUILD.md names base image %s, base-image.sh pins %s" % (n, pinned) for n in sorted(names) if n != pinned]


# ================================================================ tests
def test_doc_set():
    probs = set_problems(lambda p: os.path.exists(os.path.join(ROOT, p)), read("README.md"))
    check(probs == [], "customer doc set complete and linked from README %s" % probs)
    broken = set_problems(lambda p: p != "docs/FAQ.md", read("README.md").replace("](docs/FAQ.md)", "](FAQ)"))
    check(len(broken) == 2, "BROKEN: a missing FAQ.md and its dead README link are both reported %s" % broken)


def test_banners_at_top():
    T = art.TEXT
    probs = (banner_problems("README.md", read("README.md"), T["warning_label"], T["warning"]) +
             banner_problems("docs/BUILD.md", read("docs/BUILD.md"), T["warning_label"], T["warning"]) +
             banner_problems("README.md", read("README.md"), T["ai_label"], T["ai"]))
    check(probs == [], "warning (README, BUILD) and AI note (README) at the top, alt = plain text %s" % probs)
    readme = read("README.md")
    low = "\n" * TOP_LINES + readme
    check(banner_problems("README.md", low, T["warning_label"], T["warning"]) != [],
          "BROKEN: a warning pushed below line %d is reported" % TOP_LINES)
    alt = readme.replace("WARNING: This is not production", "WARNING: This is not really production", 1)
    check(banner_problems("README.md", alt, T["warning_label"], T["warning"]) != [],
          "BROKEN: a changed banner alt text is reported")
    copy = readme.replace("> **A note on AI.** Yes, I used AI", "> **A note on AI.** Yes, we used AI", 1)
    check(banner_problems("README.md", copy, T["ai_label"], T["ai"]) != [],
          "BROKEN: a plain-text AI note that drifts from the banner is reported")


def test_reference_lists_every_target():
    targets = help_targets()
    if targets is None:
        skip("make help does not run here")
        return
    probs = target_problems(read("docs/REFERENCE.md"), targets)
    check(probs == [], "REFERENCE.md lists all %d make help targets %s" % (len(targets), probs))
    broken = target_problems(read("docs/REFERENCE.md").replace("`make net-check`", "`net-check`"), targets)
    check(any("net-check" in p for p in broken), "BROKEN: a target dropped from REFERENCE.md is reported %s" % broken)


def test_roms_systems_match_launcher():
    code, doc = launcher_systems(), roms_systems(read("docs/ROMS.md"))
    check(len(code) >= 10, "launch.py CORE_BY_SYSTEM parsed (%d systems)" % len(code))
    probs = system_problems(doc, code)
    check(probs == [], "ROMS.md lists exactly the launcher's systems %s" % probs)
    broken = system_problems(doc - {"saturn"} | {"psp"}, code)
    check(len(broken) == 2, "BROKEN: an extra and a missing system are both reported %s" % broken)


def test_tuning_sources():
    text = read("docs/TUNING.md")
    probs = tuning_problems(text)
    check(probs == [], "every at-a-glance decision in TUNING.md names its source %s" % probs)
    broken = tuning_problems(text.replace("| #213, 2026-11-18 |", "| 2026-11-18 |", 1))
    check(len(broken) == 1, "BROKEN: a decision row without a ticket is reported %s" % broken)


def test_tbd_markers():
    probs, markers = [], []
    for rel in CUSTOMER_DOCS:
        t = read(rel)
        probs += tbd_problems(rel, t)
        markers += tbd_markers(rel, t)
    check(probs == [], "every open gap is a `TBD (#N)` marker %s" % probs)
    print("INFO open TBD markers: %d (%s)" % (len(markers), ", ".join(sorted({m[2] for m in markers}))))
    check(tbd_problems("x.md", "the size is TBD later") != [], "BROKEN: a bare TBD is reported")
    check(tbd_problems("x.md", "TBD (#237): the size") == [], "a `TBD (#N)` marker passes")


def test_badges_reproducible():
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "docs", "assets", "gen_docs_art.py"), "--badges-only",
                            tmp], capture_output=True, text=True, timeout=60)
        check(r.returncode == 0, "gen_docs_art.py --badges-only runs (stdlib) %s" % r.stderr.strip())
        probs = badge_drift(os.path.join(ROOT, "docs", "assets"), tmp)
        check(probs == [], "committed SVG badges equal a fresh render %s" % probs)
        name = sorted(art.BADGES)[0]
        with open(os.path.join(tmp, name), "a") as f:
            f.write("<!-- drift -->\n")
        broken = badge_drift(os.path.join(ROOT, "docs", "assets"), tmp)
        check(len(broken) == 1, "BROKEN: a badge that differs from its render is reported %s" % broken)


def test_banners_render():
    try:
        from PIL import Image
    except ImportError:
        skip("Pillow missing: PNG banners not re-rendered (run with ~/.venvs/badges/bin/python)")
        return
    import brand
    b = brand.load()
    fresh = {"header.png": art.header_banner(b),
             "warning.png": art.note_banner(b, art.TEXT["warning_label"], art.TEXT["warning"], "red_light", "bg"),
             "ai-note.png": art.note_banner(b, art.TEXT["ai_label"], art.TEXT["ai"], "gold", "on_gold")}
    for name, img in sorted(fresh.items()):
        with Image.open(os.path.join(ROOT, "docs", "assets", name)) as committed:
            check(committed.size == img.size, "%s: committed size %s = fresh render %s" % (name, committed.size, img.size))


def test_support_line():
    readme = read("README.md")
    check(support_problems(readme) == [], "README 'Fork it, make it yours' ends with the coffee line %s"
          % support_problems(readme))
    coffee = "**[Buy me a coffee on Ko-fi](%s).**" % kofi_page()
    check(coffee in readme, "the coffee line links the FUNDING.yml Ko-fi page")
    kofi = readme.replace(coffee, "**Buy me a coffee:** https://ko-fi.com/someone")
    check(support_problems(kofi) != [], "BROKEN: a Ko-fi link to another page is reported")
    paypal = readme.replace(coffee, coffee + " Or https://paypal.me/someone")
    check(support_problems(paypal) != [], "BROKEN: another tip site next to the Ko-fi link is reported")
    early = readme.replace("open an issue on the project's repo", "open an issue on the project's repo (%s)" % kofi_page())
    check(early != readme and support_problems(early) != [], "BROKEN: a tip link above the coffee line is reported")
    gone = readme.replace(coffee, "**Thanks.**")
    check(support_problems(gone) != [], "BROKEN: a section without the coffee line is reported")
    later = readme.replace(coffee, coffee + "\n\nPlease donate, it really helps!")
    check(support_problems(later) != [], "BROKEN: a pitch after the coffee line is reported")
    noissue = readme.replace("open an issue on the project's repo", "write to me")
    check(support_problems(noissue) != [], "BROKEN: no repo-issue contact is reported")


def test_build_claims_match_the_image():
    text = read("docs/BUILD.md")
    probs = image_claim_problems(text)
    check(probs == [], "BUILD.md: no TBD (#244) left, Fightcade in the image only with hk_image_fightcade %s" % probs)
    check("TBD (#244)" not in read("README.md"), "README.md: no TBD (#244) left")
    planted = text.replace("## What the image gives today\n", "## What the image gives today\n\nThe boot menu: TBD (#244).\n", 1)
    check(planted != text and image_claim_problems(planted) != [], "BROKEN: a TBD (#244) marker left behind is reported")
    row = text.replace("| Fightcade (Box64, Wine, the lobby) | yes by default (`hk_image_fightcade: false` leaves it out)", "| Fightcade (Box64, Wine, the lobby) | yes", 1)
    check(row != text and image_claim_problems(row) != [], "BROKEN: a Fightcade row without hk_image_fightcade is reported")


def test_platform_note():
    probs = []
    for rel in ("README.md", "docs/BUILD.md"):
        probs += platform_problems(rel, read(rel))
    check(probs == [], "README and BUILD open with 'Platform: tested on macOS', Linux not tested yet, Windows later %s" % probs)
    build = read("docs/BUILD.md")
    gone = re.sub(r"^> \*\*Platform:.*\n(>.*\n)*", "", build, flags=re.M)
    check(gone != build and platform_problems("BUILD.md", gone) != [], "BROKEN: a BUILD.md without the platform note is reported")
    linux = build.replace("Linux: the build is written for it but not tested yet", "Linux: works", 1)
    check(linux != build and platform_problems("BUILD.md", linux) != [], "BROKEN: a note that drops 'Linux not tested yet' is reported")
    claim = build.replace("tested on macOS (Apple Silicon) so far.", "tested on macOS (Apple Silicon) and tested on Windows.", 1)
    check(claim != build and platform_problems("BUILD.md", claim) != [], "BROKEN: a note that calls Windows tested is reported")
    reword = build.replace("Windows: coming after Thanksgiving.", "Windows: coming after Thanksgiving, not tested on Linux ARM either.", 1)
    check(reword != build and platform_problems("BUILD.md", reword) == [], "a 'not tested on <other>' wording is not taken as a claim")
    low = "\n" * PLATFORM_TOP + read("README.md")
    check(platform_problems("README.md", low) != [], "BROKEN: a platform note pushed below line %d is reported" % PLATFORM_TOP)


def test_base_image_name_follows_pin():
    pinned, build = pinned_base_name(), read("docs/BUILD.md")
    check(pinned is not None and pinned.endswith(".img"), "base-image.sh pins %s" % pinned)
    probs = base_name_problems(build, pinned)
    check(probs == [], "BUILD.md's beginner steps name the pinned base image %s" % probs)
    if pinned:
        stale = build.replace(pinned, "2025-05-13-raspios-bookworm-arm64-lite.img", 1)
        check(stale != build and len(base_name_problems(stale, pinned)) == 1,
              "BROKEN: a base image name that differs from the pin is reported")


def test_md5s():
    roms = read("docs/ROMS.md")
    check(md5_problems(roms) == [], "BIOS md5s in ROMS.md are 32 lowercase hex %s" % md5_problems(roms))
    broken = roms.replace("`e10c53c2f8b90bab96ead2d368858623`", "`e10c53c2f8b90bab96ead2d36885862`")
    check(len(md5_problems(broken)) == 1, "BROKEN: a 31-digit md5 is reported")


if __name__ == "__main__":
    main(globals())
