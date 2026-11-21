#!/usr/bin/env python3
"""Compliance gate (#239): THIRD_PARTY.md lists what the build installs, and the legal docs carry the caveat.

  1. every libretro core the image installs (#244: launch.py's table, ansible/roles/cores; was `for c in ...` in skills/build-image/scripts/chroot-setup.sh)
     has a row in THIRD_PARTY.md naming it as `core`;
  2. each non-commercial core (NONCOMMERCIAL) has its own row in the "non-commercial components" table at the
     top, so a reader sees the restriction before anything else, and its row in the lower tables says so;
  3. every upstream download in the build scripts (build/*/fetch.sh, build/*/build.sh, chroot-setup.sh) and in
     the Ansible roles (ansible/roles/*, tests excluded) is named in THIRD_PARTY.md: the host, and for GitHub
     the full owner/repo;
  4. docs/LEGAL.md, THIRD_PARTY.md and COMPLIANCE.md say "Not legal advice; I'm not a lawyer";
  5. docs/LEGAL.md names the non-affiliation and the bring-your-own-dumps rule.
Broken cases (test_broken_*) run the same checks on planted text and must find the problem.
Plain python (hktest).
"""
import glob
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
from hktest import check, main  # noqa: E402

CHROOT = os.path.join(ROOT, "skills", "build-image", "scripts", "chroot-setup.sh")
THIRD = os.path.join(ROOT, "THIRD_PARTY.md")
LEGAL = os.path.join(ROOT, "docs", "LEGAL.md")
COMPLIANCE = os.path.join(ROOT, "COMPLIANCE.md")
CAVEAT = "not legal advice; i'm not a lawyer"
NONCOMMERCIAL = ("fbneo", "mame2003_plus", "snes9x", "genesis_plus_gx")
URL = re.compile(r"https://([A-Za-z0-9.-]+)(?:/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?)?")
GITHUB = ("github.com", "codeload.github.com", "raw.githubusercontent.com")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


TABLE = os.path.join(ROOT, "ansible", "roles", "cores", "files", "launcher_table.py")


def build_cores(script_text=None):
    """The cores the image installs (#244: role `cores`, from launch.py's own table via launcher_table.py)."""
    import subprocess
    r = subprocess.run([sys.executable, "-I", TABLE, "--format", "cores"], capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else []


def missing_cores(cores, third_text):
    return [c for c in cores if "`%s`" % c not in third_text]


def noncommercial_table(third_text):
    """The text of the 'non-commercial components' section (up to the next ## heading)."""
    m = re.search(r"(?ms)^## [^\n]*non-commercial[^\n]*\n(.*?)(?=^## )", third_text)
    return m.group(1) if m else ""


def table_rows(text):
    return [ln for ln in text.splitlines() if ln.startswith("|")]


def missing_noncommercial(third_text):
    """Cores without their own top-table row, or whose lower-table row does not say Non-commercial."""
    top = noncommercial_table(third_text)
    rest = third_text.replace(top, "")
    miss = []
    for c in NONCOMMERCIAL:
        tag = "`%s`" % c
        in_top = [r for r in table_rows(top) if tag in r]
        own = [r for r in in_top if not any("`%s`" % o in r for o in NONCOMMERCIAL if o != c)]
        lower = [r for r in table_rows(rest) if tag in r]
        if not own or not lower or not all("non-commercial" in r.lower() for r in lower):
            miss.append(c)
    return miss


def download_hosts(texts):
    """Every https download source: the host, or for GitHub hosts `owner/repo` (a `.git` suffix dropped)."""
    out = set()
    for t in texts:
        for host, owner, repo in URL.findall(t):
            if host in GITHUB:
                if owner and repo:
                    out.add("%s/%s" % (owner, re.sub(r"\.git$", "", repo)))
            else:
                out.add(host)
    return out


def missing_hosts(hosts, third_text):
    low = third_text.lower()
    return [h for h in sorted(hosts) if h.lower() not in low]


FONT_DIRS = (os.path.join("skills", "badges", "fonts"), os.path.join("themes", "coin-dojo", "_inc", "fonts"))


def font_problems(root, third_text):
    """Vendored fonts (#259): each .ttf/.otf has an OFL-*.txt next to it, and THIRD_PARTY.md names its family."""
    out = []
    for d in FONT_DIRS:
        folder = os.path.join(root, d)
        names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        fonts = [n for n in names if n.lower().endswith((".ttf", ".otf"))]
        if any(n.startswith("OFL-") for n in names) is False and fonts:
            out.append("%s: fonts without an OFL-*.txt licence file" % d)
        for n in fonts:
            family = n.split("-")[0].split("_")[0]
            if not any(o.startswith("OFL-" + family) for o in names):
                out.append("%s: %s has no licence file" % (d, n))
            if family.lower() not in third_text.lower().replace(" ", ""):
                out.append("%s: THIRD_PARTY.md does not name %s" % (d, family))
    return out


def lacks_caveat(text):
    return CAVEAT not in text.lower().replace("’", "'")


def _download_texts():
    paths = [CHROOT] + sorted(glob.glob(os.path.join(ROOT, "build", "*", "fetch.sh"))) + \
        sorted(glob.glob(os.path.join(ROOT, "build", "*", "build.sh"))) + \
        sorted(glob.glob(os.path.join(ROOT, "build", "*", "pins.json")))   # #267: build/fightcade's urls live there
    for dirpath, dirs, files in os.walk(os.path.join(ROOT, "ansible", "roles")):
        dirs[:] = sorted(d for d in dirs if d != "tests")
        paths += [os.path.join(dirpath, f) for f in sorted(files)]
    out = []
    for p in paths:
        try:
            out.append(_read(p))
        except UnicodeDecodeError:
            pass
    return out


def test_every_build_core_has_a_row():
    cores = build_cores()
    check(len(cores) >= 5, "the image's cores are read from launch.py (%d cores)" % len(cores))
    miss = missing_cores(cores, _read(THIRD))
    check(not miss, "THIRD_PARTY.md has a row for every core the image build fetches %s" % (miss or ""))


def test_noncommercial_cores_are_flagged_first():
    miss = missing_noncommercial(_read(THIRD))
    check(not miss, "the non-commercial table at the top names %s %s" % (", ".join(NONCOMMERCIAL), miss or ""))


def test_every_download_host_is_named():
    hosts = download_hosts(_download_texts())
    check(len(hosts) >= 5, "download sources found in the build scripts and roles (%d)" % len(hosts))
    check("libretro/glsl-shaders" in hosts and "packages.es-de.org" in hosts,
          "the Ansible roles' downloads (glsl-shaders, ES-DE) are scanned")
    miss = missing_hosts(hosts, _read(THIRD))
    check(not miss, "THIRD_PARTY.md names every upstream the build downloads from %s" % (miss or ""))


def test_legal_docs_carry_the_caveat():
    for p in (LEGAL, THIRD, COMPLIANCE):
        check(not lacks_caveat(_read(p)), "%s says: Not legal advice; I'm not a lawyer" % os.path.relpath(p, ROOT))


def test_vendored_fonts_are_licensed_and_listed():
    check(os.path.isfile(os.path.join(ROOT, "skills", "badges", "fonts", "YujiSyuku-dojo-subset.ttf")),
          "the Yuji Syuku subset is vendored for gen_backdrop.py (#259)")
    probs = font_problems(ROOT, _read(THIRD))
    check(not probs, "every vendored font has its OFL text and a THIRD_PARTY.md row %s" % (probs or ""))
    check("OFL-1.1" in _read(THIRD) and "yujisyuku" in _read(THIRD).lower(), "THIRD_PARTY.md gives the Yuji Syuku licence and source")


def test_legal_page_names_affiliation_and_dumps():
    t = _read(LEGAL).lower()
    check("not affiliated" in t, "docs/LEGAL.md says the project is not affiliated")
    check("dumps you are entitled to" in t, "docs/LEGAL.md says to use only dumps you are entitled to")
    check("third_party.md" in t, "docs/LEGAL.md links THIRD_PARTY.md")


# ------------------------------------------------------------------------------------------- broken cases

def test_broken_new_core_without_row():
    third = _read(THIRD).replace("`mednafen_pce` core", "core")   # a launcher core whose row is gone
    miss = missing_cores(build_cores(), third)
    check(miss == ["mednafen_pce"], "BROKEN: a core added to the build without a row is caught (%s)" % miss)


NC_GOOD = ("## Read this first: non-commercial components\n\n| `fbneo` | x |\n| `mame2003_plus` | x |\n"
           "| `snes9x` | x |\n| `genesis_plus_gx` | x |\n\n## Front end\n\n| `fbneo` core | **Non-commercial** |\n"
           "| `mame2003_plus` core | **Non-commercial** |\n| `snes9x` core | **Non-commercial** |\n"
           "| `genesis_plus_gx` core | **Non-commercial** |\n")


def test_noncommercial_check_accepts_a_good_layout():
    check(missing_noncommercial(NC_GOOD) == [], "a layout with one top row per core and flagged lower rows passes")


def test_broken_noncommercial_core_only_in_the_lower_table():
    text = NC_GOOD.replace("| `snes9x` | x |\n", "")
    check(missing_noncommercial(text) == ["snes9x"], "BROKEN: a non-commercial core missing from the top table is caught")


def test_broken_noncommercial_cores_sharing_one_row():
    text = NC_GOOD.replace("| `mame2003_plus` | x |\n| `snes9x` | x |\n", "| `mame2003_plus`, `snes9x` | x |\n")
    check(missing_noncommercial(text) == ["mame2003_plus", "snes9x"],
          "BROKEN: two non-commercial cores sharing one top row are caught (%s)" % missing_noncommercial(text))


def test_broken_lower_row_not_flagged():
    text = NC_GOOD.replace("| `snes9x` core | **Non-commercial** |", "| `snes9x` core | GPL |")
    check(missing_noncommercial(text) == ["snes9x"], "BROKEN: a lower-table row that drops the flag is caught")


def test_broken_new_download_host():
    hosts = download_hosts(['URL="https://github.com/someone/thing/releases/x.tar.gz"\n',
                            "curl https://example.org/file\n", "git clone https://github.com/ptitSeb/box64.git\n",
                            "url: https://raw.githubusercontent.com/ptitSeb/other/{{ x }}\n"])
    check(hosts == {"someone/thing", "example.org", "ptitSeb/box64", "ptitSeb/other"},
          "URLs reduce to host or owner/repo (%s)" % sorted(hosts))
    miss = missing_hosts(hosts, "from `github.com/ptitSeb/box64`\n")
    check(miss == ["example.org", "ptitSeb/other", "someone/thing"],
          "BROKEN: unlisted sources are caught, a listed owner does not cover another repo (%s)" % miss)


def test_broken_font_without_licence_or_row():
    with tempfile.TemporaryDirectory() as t:
        fd = os.path.join(t, "skills", "badges", "fonts")
        os.makedirs(fd)
        open(os.path.join(fd, "YujiSyuku-dojo-subset.ttf"), "wb").write(b"x")
        check(any("licence" in p for p in font_problems(t, "Yuji Syuku")),
              "BROKEN: a vendored font without its OFL text is caught")
        open(os.path.join(fd, "OFL-YujiSyuku.txt"), "w").write("OFL")
        check(font_problems(t, "Yuji Syuku OFL-1.1") == [], "a licensed, listed font passes")
        check(any("does not name" in p for p in font_problems(t, "other fonts only")),
              "BROKEN: a vendored font missing from THIRD_PARTY.md is caught")
        open(os.path.join(fd, "NotoSans-x.ttf"), "wb").write(b"\0")
        check(any("NotoSans-x.ttf has no licence" in p for p in font_problems(t, "Yuji Syuku NotoSans")),
              "BROKEN: a second font is not covered by another font's OFL file")


def test_broken_missing_caveat():
    check(lacks_caveat("# Legal\nThis is fine, trust us.\n"), "BROKEN: a legal page without the caveat is caught")
    check(not lacks_caveat("Not legal advice; I’m not a lawyer."), "the caveat with a curly apostrophe counts")


if __name__ == "__main__":
    main(globals())
