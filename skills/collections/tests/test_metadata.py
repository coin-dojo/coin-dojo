#!/usr/bin/env python3
"""Tests for `collgen.py metadata` (metadata.py; stdlib only; pytest or run directly).

    python3 skills/collections/tests/test_metadata.py

Fills only MISSING fields/media from a RomM JSON Lines export, the libretro dat and the picks.
Broken cases: a wrong match (title guard) writes nothing for that game; an existing field or media
file is never overwritten; a malformed RomM row (bad JSON, missing fs_name, '..' path, duplicate)
exits 3 and writes nothing; a non-image or oversized staged file is refused; --fetch into a non-empty
dir exits 2; a second run is SAME.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #151)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("collgen", os.path.join(SKILL, "collgen.py"))
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
sys.path.insert(0, SKILL)
import metadata as md  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 16

FBNEO = """<?xml version="1.0"?>
<gameList>
\t<game>
\t\t<path>./contra.zip</path>
\t\t<name>Contra</name>
\t\t<releasedate>19870101T000000</releasedate>
\t</game>
\t<game>
\t\t<path>./jackal.zip</path>
\t\t<name>Jackal</name>
\t\t<desc>keep &amp; my desc</desc>
\t\t<rating>0.3</rating>
\t\t<genre>Run and Gun</genre>
\t</game>
\t<game>
\t\t<path>./wrong.zip</path>
\t\t<name>Wrong Game</name>
\t</game>
\t<game>
\t\t<path>./neogeo.zip</path>
\t\t<name>neogeo</name>
\t\t<hidden>true</hidden>
\t</game>
\t<game>
\t\t<path>./datonly.zip</path>
\t\t<name>Dat Only</name>
\t\t<genre></genre>
\t</game>
</gameList>
"""
PSX = """<?xml version="1.0"?>
<gameList>
\t<game>
\t\t<path>./Tekken 3.chd</path>
\t\t<name>Tekken 3</name>
\t</game>
</gameList>
"""
VIRT = """<?xml version="1.0"?>
<gameList>
\t<game>
\t\t<path>./x.zip</path>
\t\t<name>Contra</name>
\t</game>
</gameList>
"""


def row(platform, fs_name, name, **kw):
    d = dict(platform=platform, fs_name=fs_name, name=name, alt_names=[], summary=None, genres=[],
             release_ms=None, developers=[], publishers=[], players=None, rating=None, rating_count=None, cover=None, screenshots=[], missing=0)
    d.update(kw)
    return d


ROWS = [
    row("arcade", "contra.zip", "Contra", summary="Run & gun.\nTwo lines.", genres=["Shooter", "Platform", "Arcade"],
        release_ms=539136000000, developers=["Konami"], publishers=["Konami"], players="1-2",
        rating=80.195, rating_count=12,
        cover="roms/35/1/cover/big.png", screenshots=["roms/35/1/screenshots/0.jpg"]),
    row("arcade", "jackal.zip", "Top Gunner", alt_names=["Jackal"], summary="new desc", genres=["Shooter"], rating=95,
        players="1-2", cover="roms/35/2/cover/big.png"),
    row("arcade", "wrong.zip", "Totally Unrelated Title", summary="must not land", genres=["Puzzle"], rating=50,
        cover="roms/35/3/cover/big.png"),
    row("arcade", "neogeo.zip", "Neo Geo BIOS", summary="hidden, skipped"),
    row("psx", "Tekken 3 (USA).chd", "Tekken 3", summary="chd one", players=2),
    row("psx", "Tekken 3 (USA).m3u", "Tekken 3", summary="m3u one"),
]
DAT = '''game (
\tname "Dat Only Game"
\treleaseyear "1990"
\tpublisher "Data East"
\trom ( name datonly.zip size 1 crc 0 )
)
game (
\tname "Contra (US)"
\treleaseyear "1987"
\tpublisher "Konami"
\trom ( name contra.zip size 1 crc 0 )
)
'''
PICKS = 'const PICKS=[["Contra","arc","rng","80s",1]];'


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cg.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def world(rows=ROWS, raw=None):
    root = tempfile.mkdtemp()
    for s, t in (("fbneo", FBNEO), ("psx", PSX), ("must-play", VIRT)):
        os.makedirs(os.path.join(root, "gl", s))
        open(os.path.join(root, "gl", s, "gamelist.xml"), "w", encoding="utf-8").write(t)
    open(os.path.join(root, "romm.jsonl"), "w", encoding="utf-8").write(
        raw if raw is not None else "".join(json.dumps(r) + "\n" for r in rows))
    open(os.path.join(root, "lib.dat"), "w", encoding="utf-8").write(DAT)
    open(os.path.join(root, "picks.html"), "w", encoding="utf-8").write(PICKS)
    st = os.path.join(root, "staging")
    for rel, data in (("roms/35/1/cover/big.png", PNG), ("roms/35/1/screenshots/0.jpg", PNG),   # PNG named .jpg
                      ("roms/35/2/cover/big.png", JPG), ("roms/35/3/cover/big.png", PNG)):
        os.makedirs(os.path.dirname(os.path.join(st, rel)), exist_ok=True)
        open(os.path.join(st, rel), "wb").write(data)
    os.makedirs(os.path.join(root, "media", "fbneo", "covers"))
    open(os.path.join(root, "media", "fbneo", "covers", "jackal.jpg"), "wb").write(b"OLD")   # existing: kept
    return root


def args(root, *extra):
    return ["metadata", "--gamelists", os.path.join(root, "gl"), "--romm", os.path.join(root, "romm.jsonl"),
            "--dat", os.path.join(root, "lib.dat"), "--picks", os.path.join(root, "picks.html"),
            "--media", os.path.join(root, "media"), "--staging", os.path.join(root, "staging")] + list(extra)


def rd(root, s):
    return open(os.path.join(root, "gl", s, "gamelist.xml"), encoding="utf-8").read()


def block(text, path):
    return cg._block_for(text, path)[2]


def test_plan_writes_nothing():
    root = world()
    rc, out, _ = run(*args(root))
    assert rc == 0 and "plan only" in out and "CHANGED" in out
    assert rd(root, "fbneo") == FBNEO and rd(root, "psx") == PSX
    assert not os.path.exists(os.path.join(root, "media", "fbneo", "screenshots"))


def test_apply_fills_missing_fields_and_backs_up():
    root = world()
    rc, out, _ = run(*args(root, "--apply", "--backup-dir", os.path.join(root, "bak"), "--stamp", "s1"))
    assert rc == 0 and "applied: 2 gamelist(s)" in out
    assert open(os.path.join(root, "bak", "s1", "fbneo", "gamelist.xml"), encoding="utf-8").read() == FBNEO
    b = block(rd(root, "fbneo"), "./contra.zip")
    assert "<desc>Run &amp; gun.\nTwo lines.</desc>" in b
    assert "<developer>Konami</developer>" in b and "<publisher>Konami</publisher>" in b
    assert "<players>1-2</players>" in b
    assert "<genre>Run and Gun</genre>" in b                      # picks code wins over RomM's list
    assert b.count("<releasedate>") == 1 and "19870101T000000" in b   # existing value kept
    # ES-DE element order: desc after name, genre after publisher, players last
    assert "<rating>0.8</rating>" in b and "rating<-romm 1" in out
    assert b.index("<name>") < b.index("<desc>") < b.index("<rating>") < b.index("<releasedate>") < b.index("<developer>") \
        < b.index("<publisher>") < b.index("<genre>") < b.index("<players>")


def test_only_adds_lines():
    root = world()
    run(*args(root, "--apply", "--stamp", "s1"))
    new = rd(root, "fbneo").splitlines()
    old = FBNEO.splitlines()
    it = iter(new)
    assert all(any(o == n for n in it) for o in old)            # every original line survives, in order


def test_existing_field_never_overwritten():                         # BROKEN CASE guard
    root = world()
    run(*args(root, "--apply", "--stamp", "s1"))
    b = block(rd(root, "fbneo"), "./jackal.zip")
    assert "<desc>keep &amp; my desc</desc>" in b and "new desc" not in b
    assert "<genre>Run and Gun</genre>" in b and b.count("<genre>") == 1
    assert "<players>1-2</players>" in b                            # jackal matched via alt_names
    assert "<rating>0.3</rating>" in b and b.count("<rating>") == 1  # RomM 95 never replaces it


def test_rating_conversion():
    assert md.es_rating(80.195) == "0.8" and md.es_rating(70) == "0.7" and md.es_rating(100) == "1"
    assert md.es_rating(0) == "0" and md.es_rating(66) == "0.65" and md.es_rating(67.6) == "0.7"
    assert md.es_rating(2.4) == "0" and md.es_rating(2.6) == "0.05" and md.es_rating(None) is None
    assert md.es_rating(12.5) == "0.15" and md.es_rating(97.5) == "1"


def test_empty_rating_filled_and_second_run_same():
    root = world()
    open(os.path.join(root, "gl", "psx", "gamelist.xml"), "w", encoding="utf-8").write(
        PSX.replace("<name>Tekken 3</name>", "<name>Tekken 3</name>\n\t\t<rating></rating>"))
    rows = ROWS[:4] + [dict(ROWS[4], rating=70)] + ROWS[5:]
    open(os.path.join(root, "romm.jsonl"), "w").write("".join(json.dumps(r) + "\n" for r in rows))
    run(*args(root, "--apply", "--stamp", "s1"))
    b = block(rd(root, "psx"), "./Tekken 3.chd")
    assert "<rating>0.7</rating>" in b and b.count("<rating>") == 1
    rc, out, _ = run(*args(root, "--apply", "--stamp", "s2"))
    assert rc == 0 and "to add  SAME" in out


def test_bad_rating_refused():                                        # BROKEN CASE
    assert "rating" in _broken(json.dumps(dict(ROWS[0], rating=101)) + "\n")
    assert "rating" in _broken(json.dumps(dict(ROWS[0], rating=-1)) + "\n")
    assert "rating" in _broken(json.dumps(dict(ROWS[0], rating="80")) + "\n")
    assert "rating" in _broken(json.dumps(dict(ROWS[0], rating=True)) + "\n")
    assert "rating_count" in _broken(json.dumps(dict(ROWS[0], rating_count=-3)) + "\n")


def test_empty_element_is_filled_from_dat():
    root = world()
    run(*args(root, "--apply", "--stamp", "s1"))
    b = block(rd(root, "fbneo"), "./datonly.zip")
    assert "<developer>Data East</developer>" in b and "<publisher>Data East</publisher>" in b
    assert "<genre></genre>" in b                                   # nothing offers a genre: left empty


def test_wrong_match_writes_nothing():                               # BROKEN CASE
    root = world()
    rc, out, _ = run(*args(root, "--apply", "--stamp", "s1"))
    assert rc == 0 and "REFUSED (title guard) fbneo/wrong.zip" in out
    assert block(rd(root, "fbneo"), "./wrong.zip") == block(FBNEO, "./wrong.zip")
    assert not os.path.exists(os.path.join(root, "media", "fbneo", "covers", "wrong.png"))


def test_hidden_and_virtual_skipped():
    root = world()
    run(*args(root, "--apply", "--stamp", "s1"))
    assert block(rd(root, "fbneo"), "./neogeo.zip") == block(FBNEO, "./neogeo.zip")
    assert rd(root, "must-play") == VIRT


def test_media_copied_with_real_extension_and_never_overwritten():   # BROKEN CASE guard
    root = world()
    run(*args(root, "--apply", "--stamp", "s1"))
    m = os.path.join(root, "media", "fbneo")
    assert open(os.path.join(m, "covers", "contra.png"), "rb").read() == PNG
    assert os.path.exists(os.path.join(m, "screenshots", "contra.png"))       # RomM .jpg that is a PNG
    assert not os.path.exists(os.path.join(m, "screenshots", "contra.jpg"))
    assert open(os.path.join(m, "covers", "jackal.jpg"), "rb").read() == b"OLD"
    assert sorted(os.listdir(os.path.join(m, "covers"))) == ["contra.png", "jackal.jpg"]


def test_stem_match_prefers_same_extension():
    root = world()
    run(*args(root, "--apply", "--stamp", "s1"))
    b = block(rd(root, "psx"), "./Tekken 3.chd")
    assert "<desc>chd one</desc>" in b and "<players>2</players>" in b


def test_second_run_is_same():
    root = world()
    run(*args(root, "--apply", "--stamp", "s1"))
    snap = rd(root, "fbneo")
    rc, out, _ = run(*args(root, "--apply", "--stamp", "s2"))
    assert rc == 0 and "total: 0 field(s) + 0 media file(s) to add  SAME" in out
    assert rd(root, "fbneo") == snap and not os.path.exists(os.path.join(root, "gl", "fbneo", "gamelist.xml.bak-metadata-s2"))


def _broken(raw):
    root = world(raw=raw)
    rc, _out, err = run(*args(root, "--apply", "--stamp", "s1"))
    assert rc == 3, (rc, err)
    assert rd(root, "fbneo") == FBNEO and rd(root, "psx") == PSX
    return err


def test_malformed_json_row_refused():                               # BROKEN CASE
    assert "not JSON" in _broken(json.dumps(ROWS[0]) + "\n{not json\n")


def test_row_without_fs_name_refused():                              # BROKEN CASE
    assert "fs_name" in _broken(json.dumps(dict(ROWS[0], fs_name="")) + "\n")


def test_media_path_escape_refused():                                # BROKEN CASE
    assert "media path" in _broken(json.dumps(dict(ROWS[0], cover="../../etc/passwd")) + "\n")
    assert "media path" in _broken(json.dumps(dict(ROWS[0], screenshots=["/abs.png"])) + "\n")


def test_wrong_types_and_duplicates_refused():                       # BROKEN CASE
    assert "genres" in _broken(json.dumps(dict(ROWS[0], genres="Shooter")) + "\n")
    assert "release_ms" in _broken(json.dumps(dict(ROWS[0], release_ms="1987")) + "\n")
    assert "duplicate" in _broken(json.dumps(ROWS[0]) + "\n" + json.dumps(ROWS[0]) + "\n")


def test_bad_staged_files_refused():                                 # BROKEN CASE
    root = world()
    open(os.path.join(root, "staging", "roms/35/1/cover/big.png"), "wb").write(b"<html>not an image")
    open(os.path.join(root, "staging", "roms/35/1/screenshots/0.jpg"), "wb").write(PNG + b"\0" * 100)
    rc, out, _ = run(*args(root, "--max-bytes", "64", "--apply", "--stamp", "s1"))
    assert rc == 0 and "2 staged but refused" in out
    assert not os.path.exists(os.path.join(root, "media", "fbneo", "screenshots"))
    assert not [f for f in os.listdir(os.path.join(root, "media", "fbneo", "covers")) if f.startswith("contra")]


def test_want_list_when_not_staged():
    root = world()
    shutil.rmtree(os.path.join(root, "staging"))
    os.makedirs(os.path.join(root, "staging"))
    want = os.path.join(root, "want.txt")
    rc, out, _ = run(*args(root, "--want", want))
    assert rc == 0 and "not staged yet" in out
    assert open(want).read().split() == ["roms/35/1/cover/big.png", "roms/35/1/screenshots/0.jpg"]


def test_romm_genre_rule():
    assert md.romm_genre(["Shooter", "Platform", "Arcade"], cg) == "Platform"
    assert md.romm_genre(["Shooter"], cg) == "Shooter"
    assert md.romm_genre(["Fighting", "Beat'em Up"], cg) == "Fighting, Beat'em Up"
    assert cg.genre_bucket(md.romm_genre(["Fighting", "Beat'em Up"], cg)) == "beat-em-up"
    assert md.romm_genre(["Arcade"], cg) == "Arcade" and md.romm_genre([], cg) is None


def test_release_window():
    assert md.release(539136000000) == "19870201T000000"
    assert md.release(0) is None and md.release(None) is None


def test_title_guard():
    r = dict(name="Top Gunner", alt_names=["Jackal"])
    assert md.title_ok("Jackal", "jackal.zip", r, cg)
    assert not md.title_ok("Wrong Game", "wrong.zip", dict(name="Totally Unrelated Title", alt_names=[]), cg)
    assert md.title_ok("spidman", "spidman.zip", dict(name="Spider-Man", alt_names=[]), cg)  # unnamed: file name stands


def test_usage_errors():
    root = world()
    rc, _o, err = run(*args(root, "--fetch"))
    assert rc == 2 and "--thumbnails" in err
    pins = os.path.join(root, "pins.tsv")
    open(pins, "w").write("fbneo\tFBNeo_-_Arcade_Games\t" + "a" * 40 + "\n")
    rc, _o, err = run(*args(root, "--thumbnails", pins))
    assert rc == 2 and "--fetch-dir" in err
    fd = os.path.join(root, "thumbs")
    os.makedirs(fd)
    open(os.path.join(fd, "planted"), "w").write("x")
    rc, _o, err = run(*args(root, "--thumbnails", pins, "--fetch-dir", fd, "--fetch"))   # BROKEN CASE
    assert rc == 2 and "new or empty" in err


def test_bad_pins_refused():                                         # BROKEN CASE
    root = world()
    pins = os.path.join(root, "pins.tsv")
    open(pins, "w").write("fbneo\tFBNeo_-_Arcade_Games\tmaster\n")
    rc, _o, err = run(*args(root, "--thumbnails", pins, "--fetch-dir", os.path.join(root, "t")))
    assert rc == 3 and "40-hex" in err


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_validates_untrusted_download():
    d = tempfile.mkdtemp()
    ok = lambda url, timeout: _Resp(PNG)
    assert md.fetch("https://raw.githubusercontent.com/libretro-thumbnails/X/" + "a" * 40 + "/Named_Snaps/A.png",
                    os.path.join(d, "a.png"), opener=ok)
    assert not md.fetch("http://raw.githubusercontent.com/x.png", os.path.join(d, "b.png"), opener=ok)
    assert not md.fetch("https://evil.example/x.png", os.path.join(d, "c.png"), opener=ok)
    assert not md.fetch("https://raw.githubusercontent.com/x.png", os.path.join(d, "d.png"),
                        opener=lambda u, timeout: _Resp(b"#!/bin/sh\nrm -rf /\n"))
    assert not md.fetch("https://raw.githubusercontent.com/x.png", os.path.join(d, "e.png"), max_bytes=8,
                        opener=lambda u, timeout: _Resp(PNG))
    assert sorted(os.listdir(d)) == ["a.png"]


def test_thumbnail_fallback_uses_dat_name_for_arcade():
    root = world()
    pins = os.path.join(root, "pins.tsv")
    open(pins, "w").write("fbneo\tFBNeo_-_Arcade_Games\t" + "b" * 40 + "\n")
    seen = []

    def fake(url, timeout):
        seen.append(url)
        return _Resp(PNG)
    real = md.urllib.request.urlopen
    md.urllib.request.urlopen = fake
    try:
        fd = os.path.join(root, "thumbs")
        rc, out, _ = run(*args(root, "--thumbnails", pins, "--fetch-dir", fd, "--fetch", "--apply", "--stamp", "s1"))
    finally:
        md.urllib.request.urlopen = real
    assert rc == 0 and "thumbnails:" in out
    assert any(u.endswith("/Named_Boxarts/Dat%20Only%20Game.png") for u in seen)
    assert all("/" + "b" * 40 + "/" in u for u in seen)
    assert os.path.exists(os.path.join(root, "media", "fbneo", "covers", "datonly.png"))
    assert not any("contra" in u.lower() for u in seen)              # RomM media staged: no fallback fetch


def test_committed_files_are_sane():
    pins = md.parse_pins(open(os.path.join(SKILL, "thumbnails.tsv")).read(), cg)
    assert set(pins) >= {"nes", "fbneo"}
    sql = open(os.path.join(SKILL, "romm-export.sql")).read().upper()
    assert sql.lstrip().startswith("SELECT")
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "REPLACE", "TRUNCATE", "CREATE", "GRANT"):
        assert not re.search(r"\b%s\b" % verb, sql), verb             # read-only export


if __name__ == "__main__":
    main(globals(), per_test=True)
