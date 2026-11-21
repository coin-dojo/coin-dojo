#!/usr/bin/env python3
"""Tests for ra_progress.py (RetroAchievements line in ES-DE gamelists, #46) and the nested 'Has achievements'
folder. Stdlib only; fixtures are fake (made-up user, key, hashes); the live RA API is never called.

    python3 skills/collections/tests/test_ra_progress.py

Broken cases: malformed / wrong-shape / missing progress file, malformed gamelist, bad pins row, pin to an unknown
game all exit 3 with every gamelist byte-identical; a failed fetch (HTTP error, not JSON, no key, wrong user) exits 4
and keeps the old progress file; the fake user name and key never appear in any output, log or written file;
a malformed --ra-matches file exits 3.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SKILL))
GL = os.path.join(HERE, "fixtures", "gamelists")
PICKS = os.path.join(HERE, "fixtures", "picks.html")
sys.path.insert(0, SKILL)
sys.path.insert(0, os.path.join(REPO, "skills", "settings"))
import ra_progress as rp  # noqa: E402
import trophies_core as tc  # noqa: E402
import collgen as cg  # noqa: E402

T = rp.TROPHY
FAKE_USER = "fakeuser46xyz"            # made up; must never show up in output
FAKE_KEY = "FAKEkey46notREAL0000"       # made up, matches trophies_core's key shape
GENESIS_ROM = b"SEGA MEGA DRIVE fake rom bytes for a test" * 8


def md5(b):
    return hashlib.md5(b).hexdigest()


def run(fn, *argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = fn(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def snapshot(root):
    snap = {}
    for dp, _d, fns in os.walk(root):
        for f in fns:
            p = os.path.join(dp, f)
            snap[os.path.relpath(p, root)] = os.readlink(p) if os.path.islink(p) else open(p, "rb").read()
    return snap


def progress_doc():
    return {"version": 1, "fetched_at": 1, "games": {
        "100": {"title": "SSF2T", "console": 27, "total": 40, "awarded": 12, "awarded_hc": 12,
                "hashes": [md5(b"ssf2t")]},
        "101": {"title": "Metal Slug", "console": 27, "total": 30, "awarded": 5, "awarded_hc": 0,
                "hashes": [md5(b"mslug")]},
        "102": {"title": "Heroes vs Acme", "console": 27, "total": 20, "awarded": 0, "awarded_hc": 0,
                "hashes": [md5(b"mvsc")]},
        "103": {"title": "Streets of Rage 2", "console": 1, "total": 25, "awarded": 10, "awarded_hc": 4,
                "hashes": [md5(GENESIS_ROM)]},
        "104": {"title": "Pac-Man (pinned)", "console": 27, "total": 8, "awarded": 8, "awarded_hc": 8, "hashes": []},
    }}


def workspace(doc=None, user_desc=True):
    """A copy of the fixture gamelists + fake ROM root + progress file."""
    w = tempfile.mkdtemp()
    shutil.copytree(GL, os.path.join(w, "gamelists"))
    if user_desc:                                      # a desc with user text (and an entity) on ssf2t
        p = os.path.join(w, "gamelists", "cps2", "gamelist.xml")
        t = open(p, encoding="utf-8").read().replace(
            "<name>Super Avenue Fighter II Turbo</name>",
            "<name>Super Avenue Fighter II Turbo</name>\n\t\t<desc>Ninjas &amp; friends.\nSecond line.</desc>", 1)
        open(p, "w", encoding="utf-8").write(t)
    os.makedirs(os.path.join(w, "roms", "genesis"))
    with zipfile.ZipFile(os.path.join(w, "roms", "genesis", "sor2.zip"), "w") as z:
        z.writestr("Streets of Rage 2.md", GENESIS_ROM)
    with open(os.path.join(w, "progress.json"), "w", encoding="utf-8") as fh:
        json.dump(doc if doc is not None else progress_doc(), fh)
    with open(os.path.join(w, "pins.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# system\tpath\tid\nfbneo\t./pacman.zip\t104\n")
    return w


def write(w, *extra, apply=True):
    args = ["write", "--progress", os.path.join(w, "progress.json"), "--gamelists", os.path.join(w, "gamelists"),
            "--roms", os.path.join(w, "roms"), "--pins", os.path.join(w, "pins.tsv"),
            "--backup-dir", os.path.join(w, "bak"), "--stamp", "s1",
            "--matches", os.path.join(w, "matches.tsv"), "--hash-cache", os.path.join(w, "hashes.json")]
    return run(rp.main, *(args + list(extra) + (["--apply"] if apply else [])))


def gl(w, system, d="gamelists"):
    return open(os.path.join(w, d, system, "gamelist.xml"), encoding="utf-8").read()


def desc_of(text, path):
    blk = [b for b in rp.GAME_RE.findall(text) if rp.game_path(b) == path][0]
    m = rp.DESC_RE.search(blk)
    return None if m is None else (m.group(1) or "")


# ------------------------------------------------------------------------------------------------ the line

def test_trophy_line_forms():
    g = lambda t, a, h: {"total": t, "awarded": a, "awarded_hc": h}
    assert rp.trophy_line(g(40, 12, 12)) == T + " 12/40 (Hardcore)"
    assert rp.trophy_line(g(40, 12, 0)) == T + " 12/40 (Softcore)"
    assert rp.trophy_line(g(40, 12, 8)) == T + " 12/40 (8 Hardcore)"
    assert rp.trophy_line(g(40, 0, 0)) == T + " 0/40"
    assert rp.trophy_line(g(0, 0, 0)) is None
    assert rp.trophy_line(g(10, 99, 99)) == T + " 10/10 (Hardcore)"          # clipped


def test_desc_line_prepended_user_text_kept_byte_for_byte():
    blk = "<game>\n\t\t<path>./a.zip</path>\n\t\t<name>A</name>\n\t\t<desc>It&apos;s &amp; fun\n\nmore</desc>\n\t</game>"
    nb = rp.set_desc_line(blk, T + " 1/2 (Hardcore)")
    assert "<desc>" + T + " 1/2 (Hardcore)\n\nIt&apos;s &amp; fun\n\nmore</desc>" in nb
    assert rp.set_desc_line(nb, T + " 1/2 (Hardcore)") == nb                          # idempotent
    nb2 = rp.set_desc_line(nb, T + " 2/2 (Hardcore)")
    assert nb2.count(T) == 1 and T + " 2/2 (Hardcore)\n\nIt&apos;s" in nb2           # replaced, not added
    assert rp.set_desc_line(nb2, None) == blk                                          # removal restores the bytes


def test_desc_inserted_when_missing_and_removed_again():
    blk = "<game>\n\t\t<path>./a.zip</path>\n\t\t<name>A</name>\n\t</game>"
    nb = rp.set_desc_line(blk, T + " 0/9")
    assert "\t\t<name>A</name>\n\t\t<desc>" + T + " 0/9</desc>\n\t</game>" in nb
    assert rp.set_desc_line(nb, None) == blk
    assert rp.set_desc_line(blk, None) == blk
    selfc = "<game><path>./a.zip</path><desc/></game>"
    assert rp.set_desc_line(selfc, None) == selfc
    assert "<desc>" + T + " 0/9</desc>" in rp.set_desc_line(selfc, T + " 0/9")


def test_user_text_that_only_looks_similar_is_not_ours():
    blk = "<game><path>./a.zip</path><desc>" + T + " my own note about 3/4 of the game</desc></game>"
    assert rp.set_desc_line(blk, None) == blk
    nb = rp.set_desc_line(blk, T + " 1/2 (Softcore)")
    assert nb.endswith(T + " 1/2 (Softcore)\n\n" + T + " my own note about 3/4 of the game</desc></game>")


def test_duplicate_lines_from_an_old_bug_collapse_to_one():
    blk = "<game><path>./a.zip</path><desc>%s 1/2\n\n%s 1/2\n\nUser.</desc></game>" % (T, T)
    nb = rp.set_desc_line(blk, T + " 2/2 (Hardcore)")
    assert nb.count(T) == 1 and "\n\nUser.</desc>" in nb


# ------------------------------------------------------------------------------------------------ hashing

def test_hash_methods():
    rom = bytes(range(256)) * 64                                          # 16 KiB
    assert rp.hash_bytes("nes", b"NES\x1a" + b"\0" * 12 + rom) == md5(rom)
    assert rp.hash_bytes("nes", rom) == md5(rom)
    assert rp.hash_bytes("snes", b"\0" * 512 + rom) == md5(rom)
    assert rp.hash_bytes("pce", b"\0" * 512 + rom) == md5(rom)
    z64 = b"\x80\x37\x12\x40" + rom[4:]
    v64 = bytearray(z64)
    v64[0::2], v64[1::2] = z64[1::2], z64[0::2]
    n64 = bytearray(z64)
    n64[0::4], n64[1::4], n64[2::4], n64[3::4] = z64[3::4], z64[2::4], z64[1::4], z64[0::4]
    assert rp.hash_bytes("n64", z64) == rp.hash_bytes("n64", bytes(v64)) == rp.hash_bytes("n64", bytes(n64)) == md5(z64)
    h = rp.Hasher()
    assert h("fbneo", "./sf2.zip") == md5(b"sf2")                          # arcade: the file name, no file needed
    assert h("genesis", "./x.zip") is None and h("psx", "./x.chd") is None   # no --roms / no method: unknown


def test_hash_cache_is_used_and_refreshed():
    w = workspace()
    h = rp.Hasher(os.path.join(w, "roms"))
    assert h("genesis", "./sor2.zip") == md5(GENESIS_ROM) and h.dirty
    h2 = rp.Hasher(os.path.join(w, "roms"), cache=h.cache)
    assert h2("genesis", "./sor2.zip") == md5(GENESIS_ROM) and not h2.dirty
    st = os.stat(os.path.join(w, "roms", "genesis", "sor2.zip"))
    stale = {"genesis/sor2.zip": [st.st_size + 1, st.st_mtime_ns, "0" * 32]}
    assert rp.Hasher(os.path.join(w, "roms"), cache=stale)("genesis", "./sor2.zip") == md5(GENESIS_ROM)


# ------------------------------------------------------------------------------------------------ write

def test_plan_writes_nothing():
    w = workspace()
    before = snapshot(w)
    rc, out, _ = write(w, apply=False)
    assert rc == 0 and "plan only" in out and "CHANGED" in out
    assert snapshot(w) == before


def test_apply_writes_lines_and_keeps_user_text():
    w = workspace()
    rc, out, err = write(w)
    assert rc == 0, err
    cps2, neo, fb, gen = gl(w, "cps2"), gl(w, "neogeo"), gl(w, "fbneo"), gl(w, "genesis")
    assert desc_of(cps2, "./ssf2t.zip") == T + " 12/40 (Hardcore)\n\nNinjas &amp; friends.\nSecond line."
    assert desc_of(cps2, "./mvsc.zip") == T + " 0/20"
    assert desc_of(cps2, "./19xx.zip") is None                                 # hashed, no set: nothing added
    assert desc_of(neo, "./mslug.zip") == T + " 5/30 (Softcore)"
    assert desc_of(gen, "./sor2.zip") == T + " 10/25 (4 Hardcore)"            # computed from the zipped ROM
    assert desc_of(fb, "./pacman.zip") == T + " 8/8 (Hardcore)"                # pinned
    # only the desc changed: dropping it gives the original bytes back
    orig = open(os.path.join(GL, "neogeo", "gamelist.xml"), encoding="utf-8").read()
    assert neo.replace("\n\t\t<desc>" + T + " 5/30 (Softcore)</desc>", "") == orig
    assert os.path.isfile(os.path.join(w, "bak", "s1", "cps2", "gamelist.xml"))
    m = open(os.path.join(w, "matches.tsv"), encoding="utf-8").read().splitlines()
    assert m[1:] == ["cps2\t./mvsc.zip\t102\t20", "cps2\t./ssf2t.zip\t100\t40", "fbneo\t./pacman.zip\t104\t8",
                     "genesis\t./sor2.zip\t103\t25", "neogeo\t./mslug.zip\t101\t30"]
    assert "genesis/sor2.zip" in json.load(open(os.path.join(w, "hashes.json")))


def test_rerun_changes_nothing():
    w = workspace()
    assert write(w)[0] == 0
    shutil.rmtree(os.path.join(w, "bak"))
    before = snapshot(w)
    rc, out, _ = write(w, "--stamp", "s2")
    assert rc == 0 and "lines to write    0" in out and out.rstrip().endswith("0 gamelist(s) rewritten (backup first); 5 matched game(s) with a set")
    assert snapshot(w) == before                                               # no backup, cache, matches rewrite


def test_progress_update_replaces_the_line():
    w = workspace()
    write(w)
    doc = progress_doc()
    doc["games"]["100"].update(awarded=13, awarded_hc=13)
    del doc["games"]["101"]                                                    # set gone: line removed
    json.dump(doc, open(os.path.join(w, "progress.json"), "w"))
    assert write(w, "--stamp", "s2")[0] == 0
    cps2 = gl(w, "cps2")
    assert cps2.count(T) == 2 and desc_of(cps2, "./ssf2t.zip").startswith(T + " 13/40 (Hardcore)\n\nNinjas")
    assert gl(w, "neogeo") == open(os.path.join(GL, "neogeo", "gamelist.xml"), encoding="utf-8").read()


def test_unknown_hash_leaves_an_existing_line_alone():
    w = workspace()
    write(w)
    shutil.rmtree(os.path.join(w, "roms"))                                     # laptop run without the ROMs
    os.remove(os.path.join(w, "hashes.json"))
    before = snapshot(os.path.join(w, "gamelists"))
    rc, out, _ = write(w, "--stamp", "s2")
    assert rc == 0 and snapshot(os.path.join(w, "gamelists")) == before
    assert "genesis      matched    0  no set    0  unknown    5" in out


def test_given_hashes_match_disc_style_games():
    w = workspace()
    open(os.path.join(w, "h.tsv"), "w").write("genesis\t./sonic.zip\t%s\n" % md5(GENESIS_ROM).upper())
    assert write(w, "--hashes", os.path.join(w, "h.tsv"))[0] == 0
    assert desc_of(gl(w, "genesis"), "./sonic.zip") == T + " 10/25 (4 Hardcore)"


def _broken_leaves_identical(w, *extra, rc_want=3):
    before = snapshot(w)
    rc, out, err = write(w, *extra)
    assert rc == rc_want, (rc, out, err)
    assert snapshot(w) == before, "a file changed"
    return err


def test_broken_malformed_progress_json():                  # BROKEN CASE
    w = workspace()
    open(os.path.join(w, "progress.json"), "w").write('{"version": 1, "games": {')
    assert "not JSON" in _broken_leaves_identical(w)


def test_broken_progress_wrong_shape():                     # BROKEN CASE
    for bad in ({"games": {}}, {"version": 1, "games": []},
                {"version": 1, "games": {"100": {"console": 27, "total": "40"}}},
                {"version": 1, "games": {"100": {"console": 27, "total": -1}}},
                {"version": 1, "games": {"x": {"console": 27, "total": 1}}},
                {"version": 1, "games": {"100": {"console": 27, "total": 1, "hashes": ["nothex"]}}}):
        w = workspace(doc=bad)
        _broken_leaves_identical(w)


def test_broken_missing_progress_file():                    # BROKEN CASE (cache never fetched)
    w = workspace()
    os.remove(os.path.join(w, "progress.json"))
    _broken_leaves_identical(w)


def test_broken_malformed_gamelist_nothing_written():       # BROKEN CASE
    w = workspace()
    open(os.path.join(w, "gamelists", "neogeo", "gamelist.xml"), "a").write("<game><path>")
    assert "malformed XML" in _broken_leaves_identical(w)


def test_broken_pins():                                     # BROKEN CASE
    w = workspace()
    open(os.path.join(w, "pins.tsv"), "w").write("fbneo\tpacman.zip\t104\n")      # no ./
    _broken_leaves_identical(w)
    open(os.path.join(w, "pins.tsv"), "w").write("fbneo\t./pacman.zip\t999\n")     # not in progress
    assert "999" in _broken_leaves_identical(w)


def test_usage_error_exits_2():                              # BROKEN CASE
    assert run(rp.main, "write", "--gamelists", "x")[0] == 2


# ------------------------------------------------------------------------------------------------ nested

def nested_out(w, *extra):
    out = os.path.join(w, "nested_out")
    rc, o, e = run(cg.main, "nested", "--gamelists", os.path.join(w, "gamelists"), "--picks", PICKS,
                   "--out", out, "--rom-root", "/data/roms", "--min-decade", "1", "--apply", *extra)
    assert rc == 0, (o, e)
    return out, o


def test_virtual_gamelists_matched_through_links():
    w = workspace()
    out, _ = nested_out(w)
    rc, o, e = write(w, "--nested-gamelists", os.path.join(out, "gamelists"),
                     "--nested-root", os.path.join(out, "roms_nested"))
    assert rc == 0, e
    g = gl(out, "genres", d="gamelists")
    assert desc_of(g, "./fighters/ssf2t.zip") == T + " 12/40 (Hardcore)\n\nNinjas &amp; friends.\nSecond line."
    s = gl(out, "systems", d="gamelists")
    assert desc_of(s, "./neo-geo/mslug.zip") == T + " 5/30 (Softcore)"
    assert "unknown" in o and write(w, "--nested-gamelists", os.path.join(out, "gamelists"),
                                    "--nested-root", os.path.join(out, "roms_nested"), "--stamp", "s2")[1].count("CHANGED") == 0


def test_virtual_gamelists_skipped_without_nested_root():
    w = workspace()
    out, _ = nested_out(w)
    before = snapshot(os.path.join(out, "gamelists"))
    rc, o, _ = write(w, "--nested-gamelists", os.path.join(out, "gamelists"))
    assert rc == 0 and "skipped (virtual system; needs --nested-root)" in o
    assert snapshot(os.path.join(out, "gamelists")) == before


def test_has_achievements_folder_and_crossover_pointer():
    w = workspace()
    assert write(w)[0] == 0
    out, o = nested_out(w, "--ra-matches", os.path.join(w, "matches.tsv"))
    assert "achievements: 5 game(s) -> genres/achievements" in o
    g = gl(out, "genres", d="gamelists")
    assert "<path>./achievements</path>" in g and "<name>Has Achievements</name>" in g
    link = os.path.join(out, "roms_nested", "genres", "achievements", "ssf2t.zip")
    assert os.path.islink(link)                       # ssf2t is in fighters too: a #116 pointer, followed by write
    assert rp.link_target(os.path.join(out, "roms_nested"), "/data/roms", "genres",
                          "./achievements/ssf2t.zip") == ("cps2", "./ssf2t.zip")
    rc, _o, e = write(w, "--nested-gamelists", os.path.join(out, "gamelists"),
                      "--nested-root", os.path.join(out, "roms_nested"), "--stamp", "s2")
    assert rc == 0, e
    assert desc_of(gl(out, "genres", d="gamelists"), "./achievements/ssf2t.zip").startswith(T + " 12/40 (Hardcore)")


def test_no_matches_file_no_folder():
    w = workspace()
    out, o = nested_out(w)
    assert "achievements" not in o and "./achievements" not in gl(out, "genres", d="gamelists")


def test_broken_ra_matches_file_exits_3():                  # BROKEN CASE
    w = workspace()
    p = os.path.join(w, "m.tsv")
    for bad in ("cps2\tssf2t.zip\t100\t40\n", "cps2\t./ssf2t.zip\tabc\t40\n", "genres\t./x.zip\t1\t1\n", "cps2\t./a\n"):
        open(p, "w").write(bad)
        rc, _o, e = run(cg.main, "nested", "--gamelists", os.path.join(w, "gamelists"), "--picks", PICKS,
                        "--out", os.path.join(w, "no"), "--ra-matches", p)
        assert rc == 3 and "--ra-matches" in e, (bad, rc, e)
        assert not os.path.exists(os.path.join(w, "no"))


# ------------------------------------------------------------------------------------------------ fetch

def fetch_ws(key=FAKE_KEY):
    w = tempfile.mkdtemp()
    kf = os.path.join(w, "key.json")
    if key is not None:
        json.dump({"web_api_key": key}, open(kf, "w"))
    entry = os.path.join(w, "trophies.hks")
    json.dump({"user": FAKE_USER, "key_file": kf, "cache_dir": os.path.join(w, "cache")}, open(entry, "w"))
    return w, entry


def fake_api(fail=None):
    calls = []

    def get(url, **_kw):
        calls.append(url)
        if fail == "http":
            raise tc.FetchError("RetroAchievements answered HTTP 401")
        if "API_GetGameList" in url:
            if fail == "json":
                return b"<html>oops " + FAKE_USER.encode() + b"</html>"
            if "i=27" in url:
                return json.dumps([{"ID": 100, "Title": "SSF2T", "ConsoleID": 27, "NumAchievements": 40,
                                    "Hashes": [md5(b"ssf2t").upper()]}, {"ID": "junk"}]).encode()
            return b"[]"
        if "API_GetUserCompletionProgress" in url:
            if fail == "user":
                return b"{}"
            off = int(url.split("&o=")[1].split("&")[0]) if "&o=" in url else 0
            res = [{"GameID": 100, "MaxPossible": 40, "NumAwarded": 12, "NumAwardedHardcore": 12}] if off == 0 else \
                  [{"GameID": 555, "Title": "Other", "ConsoleID": 7, "MaxPossible": 10, "NumAwarded": 1, "NumAwardedHardcore": 0}]
            return json.dumps({"Count": 1, "Total": 2, "Results": res}).encode()
        raise AssertionError("unexpected url")
    return get, calls


def fetch(entry, get, *extra):
    old = tc.http_get
    tc.http_get = get
    try:
        return run(rp.main, "fetch", "--entry", entry, "--settings-dir", os.path.join(REPO, "skills", "settings"),
                   "--systems", "cps2,nes", *extra)
    finally:
        tc.http_get = old


def no_secrets(*texts):
    for t in texts:
        t = t.decode("utf-8", "replace") if isinstance(t, bytes) else t
        assert FAKE_KEY not in t and FAKE_USER not in t, "user or key leaked"


def test_fetch_writes_progress_without_user_or_key():
    w, entry = fetch_ws()
    get, calls = fake_api()
    rc, out, err = fetch(entry, get)
    assert rc == 0, err
    p = os.path.join(w, "cache", "ra-progress.json")
    doc = json.load(open(p))
    assert doc["games"]["100"] == {"title": "SSF2T", "console": 27, "total": 40, "awarded": 12, "awarded_hc": 12,
                                   "hashes": [md5(b"ssf2t")]}
    assert doc["games"]["555"]["awarded"] == 1 and len(doc["games"]) == 2              # second page read
    assert any(FAKE_KEY in c for c in calls)                                           # the key went only to the API
    no_secrets(out, err, open(p, "rb").read())
    assert rp.load_progress(p)[100]["total"] == 40                                      # write accepts it


def test_fetch_failures_keep_old_file_and_leak_nothing():  # BROKEN CASE
    for fail in ("http", "json", "user"):
        w, entry = fetch_ws()
        p = os.path.join(w, "cache", "ra-progress.json")
        os.makedirs(os.path.dirname(p))
        open(p, "w").write("OLD")
        rc, out, err = fetch(entry, fake_api(fail)[0])
        assert rc == 4, (fail, rc, err)
        assert open(p).read() == "OLD" and os.listdir(os.path.dirname(p)) == ["ra-progress.json"]
        no_secrets(out, err)


def test_fetch_without_key_calls_nothing():                 # BROKEN CASE
    w, entry = fetch_ws(key=None)
    get, calls = fake_api()
    rc, out, err = fetch(entry, get)
    assert rc == 4 and not calls and "no RetroAchievements user or web API key" in err
    assert not os.path.exists(os.path.join(w, "cache"))
    no_secrets(out, err)


def test_fetch_error_then_write_leaves_gamelists_identical():   # BROKEN CASE: the chain the cabinet runs
    w = workspace()
    fw, entry = fetch_ws()
    progress = os.path.join(w, "progress.json")
    os.remove(progress)
    assert fetch(entry, fake_api("http")[0], "--out", progress)[0] == 4
    before = snapshot(w)
    rc, out, err = write(w)
    assert rc == 3 and snapshot(w) == before
    no_secrets(out, err)


def test_unknown_system_is_usage_error():                   # BROKEN CASE
    _w, entry = fetch_ws()
    get, calls = fake_api()
    old = tc.http_get
    tc.http_get = get
    try:
        rc, _o, _e = run(rp.main, "fetch", "--entry", entry, "--systems", "nope")
    finally:
        tc.http_get = old
    assert rc == 2 and not calls


if __name__ == "__main__":
    main(globals(), per_test=True)
