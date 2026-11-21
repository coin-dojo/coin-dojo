#!/usr/bin/env python3
"""GAME LISTS (#281): lists_rebuild.py (the cabinet-side rebuild) and the SETTINGS page that runs it.

    python3 skills/settings/tests/test_lists.py

Everything runs on a throwaway tree built by lists_fixture.py (made-up titles; the real skills/collections tools). Nothing
touches a real ES-DE, /data or the Mac. Broken cases (each must leave the OLD lists byte for byte as they were):
no gamelists at all, gamelists with names only (no scrape data), a malformed gamelist, a failing build, a failing
validator, a swap that fails half way (rolled back at once), a swap whose process was killed half way (rolled back by the
next run), a game running, another rebuild running, the personal edition (refuses). Plus: the size and stars choices
change what is built, favourites carry over, the tools never leave a temp dir behind, the reload is guarded.
The page: labels, the refusal in the personal edition, the result screen, the confirm, the saved choices.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
import lists_fixture as F  # noqa: E402
import lists_rebuild as L  # noqa: E402
import settings_core as core  # noqa: E402
from hktest import check, main  # noqa: E402

FAKE_PROC = tempfile.mkdtemp(prefix="hk-fakeproc-")      # an empty process table: no game (the real /proc is not read here)
L.PROC = FAKE_PROC


class Env:
    def __init__(self, games=60, scraped=True, **kw):
        self.dir = tempfile.mkdtemp(prefix="hk-lists-")
        self.cfg = F.build(self.dir, games, scraped=scraped)
        self.cfg["virtual_systems"] = json.load(open(os.path.join(self.cfg["tools"], "virtual_systems.json")))
        self.cfg.update(kw)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def tree(self):
        """sha256 of everything the rebuild may replace: nested_out, ES-DE's virtual gamelists + es_systems.xml, the links."""
        h = hashlib.sha256()
        roots = [self.cfg["nested_out"], self.cfg["custom_systems"]] + [os.path.join(self.cfg["gamelists"], v) for v in self.cfg["virtual_systems"]]
        for root in roots:
            for d, dirs, files in os.walk(root):
                dirs.sort()
                for n in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
                    p = os.path.join(d, n)
                    h.update(p.encode())
                    if os.path.islink(p):
                        h.update(os.readlink(p).encode())
                    elif os.path.isfile(p):
                        h.update(open(p, "rb").read())
        for link in (self.cfg["nested_root"],):
            h.update(os.readlink(link).encode() if os.path.islink(link) else b"none")
        return h.hexdigest()

    def leftovers(self):
        parent = os.path.dirname(self.cfg["nested_out"])
        return [n for n in os.listdir(parent) if n.startswith(".lists-build-")] + \
               [n for n in (os.listdir(self.cfg["backup_root"]) if os.path.isdir(self.cfg["backup_root"]) else []) if n.endswith(".json")]


def refuses(env, frag, why):
    """The rebuild stops with a message containing FRAG and the old lists are untouched."""
    before = env.tree()
    try:
        L.rebuild(env.cfg)
        ok, msg = False, "no error"
    except L.ListsError as e:
        ok, msg = frag in str(e), str(e)
    check(ok, "%s: refused with %r (%s)" % (why, frag, msg))
    check(env.tree() == before, "%s: the old lists are byte for byte as they were" % why)
    check(env.leftovers() == [], "%s: no temp dir and no journal left behind (%s)" % (why, env.leftovers()))


def test_rebuild_writes_the_lists_and_reports():
    e = Env()
    try:
        res = L.rebuild(e.cfg)
        check(res["games"] > 0 and res["must_play"] > 0 and res["genres"] > 0 and res["decades"] > 0, "counts: %s" % res)
        check(res["games"] == 60 // len(F.SYSTEMS) * len(F.SYSTEMS), "SYSTEMS holds every game once (%d)" % res["games"])
        for v in ("must-play", "genres", "decades", "systems"):
            check(os.path.isfile(os.path.join(e.cfg["gamelists"], v, "gamelist.xml")), "ES-DE got its %s gamelist" % v)
        check(os.path.isfile(os.path.join(e.cfg["custom_systems"], "es_systems.xml")), "ES-DE got custom_systems/es_systems.xml")
        check(os.path.islink(e.cfg["nested_root"]) and os.path.isdir(e.cfg["nested_root"]), "the nested link resolves")
        check(os.path.isdir(os.path.join(e.cfg["nested_out"], "roms_nested", "must-play")), "roms_nested/must-play exists")
        check(L.read_state(e.cfg["state"])["lists_last"]["games"] == res["games"], "the result is saved in the state file")
        check(e.leftovers() == [], "no temp dir, no journal: %s" % e.leftovers())
        res2 = L.rebuild(e.cfg)
        check({k: res2[k] for k in ("games", "must_play", "genres", "decades")} == {k: res[k] for k in ("games", "must_play", "genres", "decades")},
              "a second rebuild gives the same counts")
    finally:
        e.close()


def test_size_and_stars_choices_apply():
    e = Env(games=240)
    try:
        sizes = {}
        for top in L.SIZES:
            L.write_state(e.cfg["state"], must_play_top=top)
            sizes[top] = L.rebuild(e.cfg)["must_play"]
        check(sizes[10] < sizes[15] < sizes[25] <= sizes[50], "MUST PLAY grows with the size: %s" % sizes)
        check(sizes[10] <= 10 * len(F.SYSTEMS), "10 per system means at most 10 each (%d)" % sizes[10])
        stars = {}
        for s in L.STARS:
            L.write_state(e.cfg["state"], must_play_genre_min_stars=s)
            stars[s] = L.rebuild(e.cfg)["must_play_genres"]
        check(stars[4.0] > stars[5.0], "MUST PLAY GENRES shrinks with the stars: %s" % stars)
        check(L.rebuild(e.cfg)["stars"] == 5.0, "the saved stars are what the build used")
        # BROKEN: a value that is not one of the choices is ignored (the defaults apply)
        L.write_state(e.cfg["state"], must_play_top=7, must_play_genre_min_stars=3.3)
        check(L.chosen(e.cfg) == (25, 4.5), "BROKEN: 7 per system / 3.3 stars in the state file fall back to 25 / 4.5")
        L.write_state(e.cfg["state"], must_play_top=True, must_play_genre_min_stars="x")
        check(L.chosen(e.cfg) == (25, 4.5), "BROKEN: a bool or a string is ignored")
    finally:
        e.close()


def test_BROKEN_no_scrape_data_changes_nothing():
    e = Env(scraped=False)
    try:
        refuses(e, "NO SCRAPED GAMES YET - SCRAPE FIRST", "names only, no metadata")
    finally:
        e.close()
    e = Env()
    try:
        L.rebuild(e.cfg)
        shutil.rmtree(e.cfg["gamelists"])
        os.makedirs(e.cfg["gamelists"])
        refuses(e, "SCRAPE FIRST", "an empty gamelists dir (after a good rebuild: the old lists stay)")
        shutil.rmtree(e.cfg["gamelists"])
        refuses(e, "SCRAPE FIRST", "no gamelists dir at all")
    finally:
        e.close()


def test_BROKEN_a_malformed_gamelist_is_named_and_changes_nothing():
    e = Env()
    try:
        L.rebuild(e.cfg)
        with open(os.path.join(e.cfg["gamelists"], "cps2", "gamelist.xml"), "w") as f:
            f.write("<gameList><game><path>./x.zip</path>")          # cut off
        refuses(e, "BAD GAMELIST: CPS2", "a gamelist that is not XML")
    finally:
        e.close()


def test_alternative_emulator_block_is_read():
    """#295: cabinet_tweaks puts <alternativeEmulator> before <gameList> (CPS1/2/3, NAOMI ...): not a bad gamelist."""
    e = Env()
    try:
        path = os.path.join(e.cfg["gamelists"], "cps2", "gamelist.xml")
        with open(path, "rb") as f:
            raw = f.read()
        decl = b'<?xml version="1.0"?>\n'
        body = raw.split(b"?>", 1)[1] if raw.lstrip().startswith(b"<?xml") else raw
        with open(path, "wb") as f:
            f.write(decl + b"<alternativeEmulator>\n\t<label>FinalBurn Neo</label>\n</alternativeEmulator>\n" + body)
        L.rebuild(e.cfg)
        check(True, "a gamelist with the alternativeEmulator block rebuilds")
        with open(path, "wb") as f:
            f.write(decl + b"<alternativeEmulator><label>x</label></alternativeEmulator>\n")
        refuses(e, "BAD GAMELIST: CPS2", "BROKEN: the block alone, no <gameList>")
    finally:
        e.close()


def test_BROKEN_failing_build_or_validator_changes_nothing():
    e = Env()
    try:
        L.rebuild(e.cfg)
        real_run = L.run
        L.run = lambda argv, cwd, env, timeout=0: (3, "input error: gamelist broke") if "nested" in argv else real_run(argv, cwd, env)
        refuses(e, "THE BUILD FAILED: INPUT ERROR", "collgen exits 3")
        L.run = lambda argv, cwd, env, timeout=0: (real_run(argv, cwd, env) if "nested" in argv else (1, "FAIL dead link x\nRESULT: FAIL"))
        refuses(e, "CHECK FAILED: FAIL DEAD LINK", "the validator gate fails")
        L.run = lambda argv, cwd, env, timeout=0: (_ for _ in ()).throw(L.ListsError("TOOK TOO LONG - NOTHING WAS CHANGED"))
        refuses(e, "TOOK TOO LONG", "a build that never finishes (timeout)")
        L.run = real_run
        e2 = dict(e.cfg, tools=os.path.join(e.dir, "no-tools"))
        e.cfg = e2
        refuses(e, "GAME LIST TOOLS MISSING", "the tools are not on the cabinet")
    finally:
        L.run = real_run
        e.close()


def test_BROKEN_a_swap_that_fails_half_way_is_rolled_back():
    e = Env()
    try:
        L.rebuild(e.cfg)
        before = e.tree()
        real = shutil.copyfile
        calls = []

        def flaky(src, dst, **kw):
            calls.append(dst)
            if len(calls) == 3:                                # the 3rd file of the swap: es_systems.xml and 2 gamelists are in
                raise OSError("disk full")
            return real(src, dst, **kw)
        shutil.copyfile = flaky
        try:
            L.rebuild(e.cfg)
            err = "no error"
        except OSError as ex:
            err = str(ex)
        finally:
            shutil.copyfile = real
        check(err == "disk full", "the failure surfaces: %s" % err)
        check(e.tree() == before, "BROKEN: a swap that fails after replacing directories and a file leaves the OLD lists")
        check(e.leftovers() == [], "no journal and no temp dir after the rollback: %s" % e.leftovers())
        check(L.rebuild(e.cfg)["games"] > 0, "and the next rebuild works")
    finally:
        e.close()


def test_BROKEN_a_killed_swap_is_rolled_back_by_the_next_run():
    e = Env()
    try:
        L.rebuild(e.cfg)
        before = e.tree()
        # a build, then a process that dies right after the directories were swapped (no undo, the journal stays)
        tmp = os.path.join(os.path.dirname(e.cfg["nested_out"]), ".lists-build-test")
        os.makedirs(tmp)
        out = L.build(e.cfg, tmp, 10, 5.0)
        sw = L.Swap(e.cfg, out, "20300101-000000")
        sw.dirs()
        sw.file(os.path.join(e.cfg["nested_out"], "custom_systems", "es_systems.xml"), os.path.join(e.cfg["custom_systems"], "es_systems.xml"))
        check(e.tree() != before, "the half swap really changed the lists")
        check(os.path.isfile(sw.journal), "the journal says what was done")
        shutil.rmtree(tmp)
        check(L.recover(e.cfg) is True, "the next run finds the journal")
        check(e.tree() == before, "BROKEN: after a kill mid-swap the next run puts the OLD lists back")
        check(not os.path.exists(sw.journal), "and removes the journal")
        check(L.recover(e.cfg) is False, "a second recover has nothing to do")
    finally:
        e.close()


def test_BROKEN_game_running_lock_and_personal_edition():
    e = Env()
    try:
        L.rebuild(e.cfg)
        real = L.game_running
        L.game_running = lambda cfg, proc="/proc": True
        try:
            refuses(e, "QUIT THE GAME FIRST", "a game is running")
        finally:
            L.game_running = real
        import fcntl
        lock = open(os.path.join(os.path.dirname(e.cfg["state"]), "lists.lock"), "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            refuses(e, "ALREADY RUNNING", "another rebuild holds the lock")
        finally:
            lock.close()
        e.cfg["edition"] = "personal"
        refuses(e, "MANAGED FROM THE MAC", "the personal edition")
        check(L.main(["status"]) == 0, "status runs")
    finally:
        e.close()
    # the CLI refuses the same way: exit 1, the line on stderr, nothing built
    e = Env()
    try:
        entry = os.path.join(e.dir, "settings.hks")
        json.dump({"lists": dict(e.cfg, edition="personal")}, open(entry, "w"))
        import io
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc, err = L.main(["rebuild", entry]), sys.stderr.getvalue()
        finally:
            sys.stderr = old
        check(rc == 1 and "MANAGED FROM THE MAC" in err and not os.path.exists(e.cfg["nested_out"]),
              "BROKEN: the personal edition CLI exits 1 and builds nothing: rc %s %r" % (rc, err.strip()))
    finally:
        e.close()


def test_BROKEN_m1_the_refusal_fails_closed():
    """No entry, a corrupt entry, one without a lists block, one whose lists block names no edition: all refuse."""
    import io
    check(L.DEFAULTS["edition"] == "personal" and L.load_config(None)["edition"] == "personal", "the built-in default is personal")
    e = Env()
    try:
        before = e.tree()
        cases = {"missing file": None, "corrupt": "{not json", "no lists block": json.dumps({"nmcli": "x"}),
                 "lists without edition": json.dumps({"lists": {k: v for k, v in e.cfg.items() if k != "edition"}}),
                 "edition nonsense": json.dumps({"lists": dict(e.cfg, edition="Community ")}),
                 "top-level list": "[1]"}
        for why, body in cases.items():
            entry = os.path.join(e.dir, "e.hks")
            if os.path.exists(entry):
                os.unlink(entry)
            if body is not None:
                open(entry, "w").write(body)
            old = sys.stderr
            sys.stderr = io.StringIO()
            try:
                rc, err = L.main(["rebuild", entry]), sys.stderr.getvalue()
            finally:
                sys.stderr = old
            if why == "edition nonsense":
                check(rc == 1, "%s: rebuild exits 1 (%s %r)" % (why, rc, err.strip()))
            else:
                check(rc == 1 and "MANAGED FROM THE MAC" in err, "%s: rebuild exits 1 with the refusal (%s %r)" % (why, rc, err.strip()))
            check(core.Lists(entry, e.cfg["state"]).managed() is True, "%s: Lists.managed() is True" % why)
            check(e.tree() == before, "%s: nothing changed" % why)
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = L.main(["rebuild"])
        finally:
            sys.stderr = old
        check(rc == 1, "no entry argument at all: exit 1")
        check(core.Lists(None, e.cfg["state"]).managed() is True and core.Lists(None, e.cfg["state"]).reload()[0] is False,
              "no entry: managed, and reload refuses")
        ok = os.path.join(e.dir, "ok.hks")
        json.dump({"lists": dict(e.cfg, edition="community")}, open(ok, "w"))
        check(core.Lists(ok, e.cfg["state"]).managed() is False, "a valid community entry is not managed")
        check(L.reload_esde(dict(e.cfg, edition="personal"), run=lambda *a, **k: 1 / 0)[1].startswith("MANAGED"), "reload refuses in personal too")
    finally:
        e.close()


def _write_journal(e, doc, raw=None):
    os.makedirs(e.cfg["backup_root"], exist_ok=True)
    j = os.path.join(e.cfg["backup_root"], "swap.journal.json")
    open(j, "w").write(raw if raw is not None else json.dumps(doc))
    return j


def test_BROKEN_m2_a_bad_journal_cannot_bite():
    e = Env()
    try:
        L.rebuild(e.cfg)
        victim = os.path.join(e.dir, "victim")
        os.makedirs(victim)
        open(os.path.join(victim, "keep.txt"), "w").write("mine")
        bk = os.path.join(e.dir, "evil-backup")
        os.makedirs(bk)
        real_dst = os.path.join(e.cfg["nested_out"], "gamelists")
        real_bk = os.path.join(e.cfg["backup_root"], "20300101-000000", "nested_out", "gamelists")
        os.makedirs(real_bk)
        open(os.path.join(real_bk, "old.txt"), "w").write("old")
        ops = [dict(kind="dir", dst=victim, bk=bk, had=True, state="done"),
               dict(kind="file", dst=os.path.join(victim, "keep.txt"), bk=os.path.join(bk, "x"), had=False, state="done"),
               dict(kind="link", dst=os.path.join(e.dir, "victim-link"), old="/etc", state="done"),
               dict(kind="dir", dst=real_dst, bk=os.path.join(e.dir, "evil-backup"), had=True, state="done"),
               dict(kind="dir", dst=real_dst, bk=real_bk, had=True, state="done")]
        j = _write_journal(e, dict(root=e.cfg["backup_root"], ops=ops))
        msgs = []
        L.recover(e.cfg, msgs.append)
        check(open(os.path.join(victim, "keep.txt")).read() == "mine" and os.path.isdir(bk) and not os.path.lexists(os.path.join(e.dir, "victim-link")),
              "BROKEN: a journal naming paths outside the lists touches none of them")
        check(os.path.isfile(os.path.join(real_dst, "old.txt")), "the legitimate op next to them was still undone")
        check(sum("skipped" in m for m in msgs) == 4, "and the skips are reported: %s" % msgs)
        check(not os.path.exists(j), "the journal is gone")
    finally:
        e.close()
    shapes = {"a list at the top level": [1, 2], "ops not a list": dict(ops="x"), "an op without a key": dict(ops=[dict(kind="dir")]),
              "an op that is a string": dict(ops=["x"]), "bad kind": dict(ops=[dict(kind="rm", dst="/")]),
              "not JSON at all": None}
    for why, doc in shapes.items():
        e = Env()
        try:
            L.rebuild(e.cfg)
            before = e.tree()
            j = _write_journal(e, doc, raw="{broken" if doc is None else None)
            msgs = []
            check(L.recover(e.cfg, msgs.append) is False, "%s: nothing is rolled back" % why)
            bad = [n for n in os.listdir(e.cfg["backup_root"]) if n.startswith("swap.journal.bad-")]
            check(len(bad) == 1 and not os.path.exists(j) and msgs, "%s: renamed to swap.journal.bad-<ts> and reported: %s %s" % (why, bad, msgs))
            check(e.tree() == before, "%s: the lists are untouched" % why)
            check(L.rebuild(e.cfg)["games"] > 0, "%s: and the next rebuild works" % why)
        finally:
            e.close()


def test_BROKEN_m2b_link_without_old_and_nul_bytes_are_malformed():
    shapes = {"a link op without its old key": dict(ops=[dict(kind="link", dst="/x", state="done")]),
              "a NUL byte in dst": dict(ops=[dict(kind="link", dst="/x\0y", old=None, state="done")]),
              "a NUL byte in bk": dict(ops=[dict(kind="file", dst="/x", bk="/y\0z", had=True, state="done")]),
              "a NUL byte in old": dict(ops=[dict(kind="link", dst="/x", old="a\0b", state="done")])}
    for why, doc in shapes.items():
        e = Env()
        try:
            L.rebuild(e.cfg)
            before = e.tree()
            j = _write_journal(e, doc)
            check(L.recover(e.cfg, lambda m: None) is False and not os.path.exists(j), "%s: parked as bad" % why)
            check(e.tree() == before and L.rebuild(e.cfg)["games"] > 0, "%s: lists untouched and the next rebuild works" % why)
        finally:
            e.close()
    check(L._journal_ops(dict(ops=[dict(kind="link", dst="/x", old=None, state="done")])) is not None, "old: null is accepted")


def test_every_accepted_journal_shape_survives_undo():
    """Table: every combination of odd values the validator accepts goes through undo() without an exception. The file
    operations are STUBS that only record what they were asked to touch (nothing real is ever deleted here)."""
    calls, boom = [], []
    real = (L.shutil.rmtree, L.os.rename, L.os.unlink, L.os.replace, L.os.symlink, L.shutil.copy2)

    def rec(name):
        def f(*a, **k):
            calls.append((name, a))
            if any(isinstance(x, str) and "\0" in x for x in a):
                raise ValueError("embedded null byte")        # what a real call does with a NUL (undo must survive it)
        return f
    L.shutil.rmtree, L.os.rename, L.os.unlink, L.os.replace, L.os.symlink, L.shutil.copy2 = (
        rec("rmtree"), rec("rename"), rec("unlink"), rec("replace"), rec("symlink"), rec("copy2"))
    try:
        vals = ["", ".", "..", "a/b", "x" * 5000, "\n", "é", "/tmp/nope", "/tmp/nope/deeper", None]
        n = ok = 0
        for kind in ("dir", "file", "link"):
            for dst in vals:
                for other in vals:
                    for had in (True, False):
                        for state in ("start", "done", "", None):
                            op = dict(kind=kind, dst=dst, state=state)
                            if kind == "link":
                                op["old"] = other
                            else:
                                op.update(bk=other, had=had)
                            n += 1
                            if L._journal_ops(dict(ops=[op])) is None:
                                continue
                            ok += 1
                            try:
                                L.undo([op])
                            except Exception as ex:                 # noqa: BLE001
                                boom.append((repr(ex), op))
    finally:
        L.shutil.rmtree, L.os.rename, L.os.unlink, L.os.replace, L.os.symlink, L.shutil.copy2 = real
    check(boom == [], "no accepted shape raises out of undo(): %s" % boom[:2])
    check(ok > 100 and calls, "%d of %d table shapes were accepted and ran through undo()" % (ok, n))
    check(L.os.rename is real[1] and L.shutil.rmtree is real[0], "the stubs are removed again")
    # BROKEN shapes the validator refuses still cannot crash undo() (also stubbed)
    L.shutil.rmtree, L.os.rename, L.os.unlink, L.os.replace, L.os.symlink, L.shutil.copy2 = (
        rec("rmtree"), rec("rename"), rec("unlink"), rec("replace"), rec("symlink"), rec("copy2"))
    try:
        L.undo([dict(kind="link", dst="/nope/q", state="done"), dict(kind="dir"), dict(kind="file", dst=5, bk=None, had=True), "x", None])
        survived = True
    except Exception:                                               # noqa: BLE001
        survived = False
    finally:
        L.shutil.rmtree, L.os.rename, L.os.unlink, L.os.replace, L.os.symlink, L.shutil.copy2 = real
    check(survived, "BROKEN: ops missing keys or of the wrong type cannot crash undo()")


def test_BROKEN_l1_a_symlinked_gamelist_folder_is_not_written_through():
    e = Env()
    try:
        L.rebuild(e.cfg)
        outside = os.path.join(e.dir, "outside")
        os.makedirs(outside)
        open(os.path.join(outside, "gamelist.xml"), "w").write("<gameList/>")
        v = os.path.join(e.cfg["gamelists"], "must-play")
        shutil.rmtree(v)
        os.symlink(outside, v)
        before = e.tree()
        try:
            L.rebuild(e.cfg)
            err = "no error"
        except L.ListsError as ex:
            err = str(ex)
        check("UNSAFE PATH" in err, "refused with a line naming the folder: %s" % err)
        check(open(os.path.join(outside, "gamelist.xml")).read() == "<gameList/>", "BROKEN: the file outside is unchanged")
        check(e.tree() == before and e.leftovers() == [], "and the rest rolled back")
        os.unlink(v)
        os.makedirs(v)
        target = os.path.join(e.dir, "target.txt")
        open(target, "w").write("t")
        os.symlink(target, os.path.join(v, ".gamelist.xml.new-%d" % os.getpid()))
        L.rebuild(e.cfg)
        check(open(target).read() == "t", "a planted temp-file symlink is replaced, never written through")
    finally:
        e.close()


def test_BROKEN_l2_a_gamelist_with_a_dtd_is_bad():
    for tag in ('<!DOCTYPE gameList [<!ENTITY x "y">]>', '<!ENTITY a "b">'):
        e = Env()
        try:
            L.rebuild(e.cfg)
            with open(os.path.join(e.cfg["gamelists"], "cps2", "gamelist.xml"), "w") as f:
                f.write(tag + "<gameList><game><path>./a.zip</path><name>&x;</name></game></gameList>")
            refuses(e, "BAD GAMELIST: CPS2", "a gamelist with %s" % tag[:10])
        finally:
            e.close()


def test_BROKEN_l3_one_guard_and_the_reload_takes_the_lock():
    e = Env()
    try:
        L.rebuild(e.cfg)
        rd = os.path.join(e.dir, "run")
        os.makedirs(rd)
        open(os.path.join(rd, "current-mode"), "w").write("fightcade\n")
        e.cfg["run_dir"] = rd
        refuses(e, "QUIT FIGHTCADE FIRST", "Fightcade is the session's mode: the REBUILD refuses too")
        os.unlink(os.path.join(rd, "current-mode"))
        import fcntl
        lock = open(os.path.join(os.path.dirname(e.cfg["state"]), "lists.lock"), "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        calls = []
        try:
            ok, msg = L.reload_esde(dict(e.cfg, ), run=lambda *a, **k: calls.append(a))
        finally:
            lock.close()
        check(not ok and calls == [] and "REBUILD IS RUNNING" in msg, "BROKEN: a reload during a rebuild waits for nothing and kills nothing: %s" % msg)
        ok, msg = L.reload_esde(dict(e.cfg, ), run=lambda *a, **k: type("R", (), dict(returncode=0))())
        check(ok, "and without the lock it goes ahead")
    finally:
        e.close()


def test_BROKEN_l4_l3_unreadable_proc_is_busy_and_the_kill_is_fixed():
    cfg = dict(game_procs=["retroarch"], game_patterns=[])
    check(L.game_running(cfg, os.path.join(FAKE_PROC, "missing")) is True, "BROKEN: an unreadable process table counts as a game running")
    check(L.game_running(dict(cfg, game_procs=None), FAKE_PROC) is True, "BROKEN: a game list that is not a list (TypeError) counts as busy")
    check(L.game_running(dict(cfg, game_patterns=[7]), FAKE_PROC) is False, "a bad pattern entry is skipped, not a crash")
    check(L.game_running(cfg, FAKE_PROC) is False, "an empty table is no game")
    e = Env()
    try:
        calls = []
        rd = os.path.join(e.dir, "run")
        os.makedirs(rd)
        ok, _m = L.reload_esde(dict(e.cfg, run_dir=rd, esde_kill="rm -rf /"), run=lambda argv, **k: calls.append(argv) or type("R", (), dict(returncode=0))(),
                               proc=FAKE_PROC)
        check(ok and calls == [["pkill", "-9", "-x", "AppRun"]], "BROKEN: a kill command in the config is ignored: %s" % calls)
        check("esde_kill" not in L.DEFAULTS, "and the key no longer exists")
    finally:
        e.close()


def test_BROKEN_virtual_names_have_one_source():
    """(#94) No typed list in lists_rebuild.py: the entry's list, else the env, else virtual_systems.json beside the tools."""
    e = Env()
    try:
        names = json.load(open(os.path.join(e.cfg["tools"], "virtual_systems.json")))
        check(L.DEFAULTS["virtual_systems"] == [], "the built-in default is empty")
        check(L.virtual_names(dict(e.cfg, virtual_systems=["a-b", "c"])) == ["a-b", "c"], "the entry's list wins")
        check(L.virtual_names(dict(e.cfg, virtual_systems=[])) == names, "else the file beside the tools")
        old = os.environ.get("HK_VIRTUAL_SYSTEMS")
        os.environ["HK_VIRTUAL_SYSTEMS"] = json.dumps(["x-y"])
        try:
            check(L.virtual_names(dict(e.cfg, virtual_systems=None)) == ["x-y"], "else the env")
        finally:
            if old is None:
                del os.environ["HK_VIRTUAL_SYSTEMS"]
            else:
                os.environ["HK_VIRTUAL_SYSTEMS"] = old
        for bad in ("must-play", ["../x"], [1], ["A B"], {"a": 1}):
            check(L.virtual_names(dict(e.cfg, virtual_systems=bad, tools=os.path.join(e.dir, "none"))) == [],
                  "BROKEN: %r is not accepted and nothing else is found" % (bad,))
        e.cfg["virtual_systems"] = []
        refuses(e, "NO VIRTUAL SYSTEM LIST", "an empty list")
    finally:
        e.close()


def test_game_running_reads_the_process_table():
    d = tempfile.mkdtemp(prefix="hk-proc-")
    try:
        cfg = dict(F.build(os.path.join(d, "x"), 12), game_patterns=["retroarch-1.22/retroarc[h]"])
        for pid, comm, cmd in (("100", "bash", "bash"), ("200", "AppRun", "AppRun"), ("300", "retroarch", "/usr/bin/retroarch")):
            os.makedirs(os.path.join(d, "proc", pid))
            open(os.path.join(d, "proc", pid, "comm"), "w").write(comm + "\n")
            open(os.path.join(d, "proc", pid, "cmdline"), "wb").write(cmd.encode().replace(b" ", b"\0"))
        proc = os.path.join(d, "proc")
        check(L.game_running(cfg, proc) is True, "retroarch by name")
        shutil.rmtree(os.path.join(proc, "300"))
        check(L.game_running(cfg, proc) is False, "no game: false")
        os.makedirs(os.path.join(proc, "400"))
        open(os.path.join(proc, "400", "comm"), "w").write("retroarch.real\n")
        open(os.path.join(proc, "400", "cmdline"), "wb").write(b"/data/emulators/retroarch-1.22/retroarch\0-L\0x")
        check(L.game_running(cfg, proc) is True, "the 1.22 binary by its command line pattern")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_favorites_and_play_counts_carry_over():
    e = Env()
    try:
        L.rebuild(e.cfg)
        gl = os.path.join(e.cfg["gamelists"], "must-play", "gamelist.xml")
        text = open(gl).read()
        i = text.index("</game>")
        text = text[:i] + "\t<favorite>true</favorite>\n\t<playcount>7</playcount>\n" + text[i:]
        open(gl, "w").write(text)
        L.rebuild(e.cfg)
        again = open(gl).read()
        check("<favorite>true</favorite>" in again and "<playcount>7</playcount>" in again,
              "a favourite and a play count of the first MUST PLAY game survive a rebuild")
    finally:
        e.close()


def test_reload_is_guarded_and_asks_the_session():
    e = Env()
    try:
        rd = os.path.join(e.dir, "run")
        os.makedirs(rd)
        cfg = dict(e.cfg, run_dir=rd)
        calls = []

        class R:
            returncode = 0
        ok, msg = L.reload_esde(cfg, run=lambda argv, **kw: calls.append(argv) or R(), proc=FAKE_PROC)
        check(ok and calls == [["pkill", "-9", "-x", "AppRun"]], "the deploy's kill command runs: %s" % calls)
        check(open(os.path.join(rd, "next-mode")).read() == "esde\n", "the session loop is asked for ES-DE first (next-mode)")
        # BROKEN: Fightcade mode counts as a running game; a game blocks the kill
        calls.clear()
        open(os.path.join(rd, "current-mode"), "w").write("fightcade\n")
        ok, msg = L.reload_esde(cfg, run=lambda argv, **kw: calls.append(argv) or R(), proc=FAKE_PROC)
        check(not ok and calls == [] and "FIGHTCADE" in msg, "BROKEN: Fightcade running -> no kill: %s" % msg)
        os.unlink(os.path.join(rd, "current-mode"))
        real = L.game_running
        L.game_running = lambda cfg, proc="/proc": True
        try:
            ok, msg = L.reload_esde(cfg, run=lambda argv, **kw: calls.append(argv) or R())
        finally:
            L.game_running = real
        check(not ok and calls == [] and "GAME" in msg, "BROKEN: a game running -> no kill: %s" % msg)
    finally:
        e.close()


# ------------------------------------------------------------------------------------------------- the page
class FakeLists(core.Lists):
    def __init__(self, entry, state, result=(True, "GAME LISTS REBUILT", None), edition="community"):
        super().__init__(entry, state, runner=self._run, spawn=self._spawn, log=core.Log(None))
        self.runs, self.spawned, self.result, self.edition = [], [], result, edition

    def cfg(self):
        c = L.load_config(None, dict(state=self.state))
        c["edition"] = self.edition
        return c

    def _run(self, argv, timeout):
        self.runs.append(argv)
        return 0, json.dumps(dict(games=1092, must_play=300, genres=1092, decades=1092, when="x")) + "\n", ""

    def _spawn(self, argv):
        self.spawned.append(argv)


def make_app(**kw):
    d = tempfile.mkdtemp(prefix="hk-listsapp-")
    ls = FakeLists(None, os.path.join(d, "state.json"), **kw)
    host = type("H", (), {"current": lambda s: "cab"})()
    app = core.App(core.NM("/bin/false", log=core.Log(None)), host, core.Log(None), None, [core.ListsPage(ls)])
    app.pending = None
    return app, ls, d


def open_page(app):
    app.stack[0]["sel"] = [i for i, (lab, _w) in enumerate(app.top["items"]) if lab == "GAME LISTS"][0]
    app.handle("ok")
    check(app.top["id"] == "lists", "GAME LISTS is on the SETTINGS home and opens")


def choose(app, label_start):
    app.top["sel"] = [i for i, (lab, _w) in enumerate(app.top["items"]) if lab.startswith(label_start)][0]
    app.handle("ok")


def test_page_rebuild_result_screen_and_reload():
    app, ls, d = make_app()
    try:
        open_page(app)
        labels = [lab for lab, _w in app.top["items"]]
        check(labels == ["REBUILD GAME LISTS", "MUST PLAY SIZE:  25 PER SYSTEM", "MUST PLAY GENRES MIN STARS:  4.5", "RELOAD ES-DE", "BACK"],
              "the page's rows: %s" % labels)
        choose(app, "REBUILD")
        check(app.pending and "REBUILDING GAME LISTS" in app.pending[0] and ls.runs == [], "a busy screen first, the child has not run yet")
        app.run_pending()
        check(len(ls.runs) == 1 and ls.runs[0][-1] == "rebuild", "the rebuild child runs once: %s" % ls.runs)
        check(app.top["id"] == "confirm" and "1092 GAMES" in app.top["text"] and "MUST PLAY 300" in app.top["text"]
              and "GENRES 1092" in app.top["text"] and "DECADES 1092" in app.top["text"], "the result screen: %s" % app.top.get("text"))
        check(app.toast[0] == "ok", "an ok toast")
        check(app.top["sel"] == 1, "NO is the default on the reload question")
        app.handle("down")
        app.handle("up")
        app.top["sel"] = 0
        app.handle("ok")                                       # YES
        check(len(ls.spawned) == 1 and ls.spawned[0][-3:-1] == ["reload", "--delay"] or "reload" in ls.spawned[0], "YES spawns the detached reload: %s" % ls.spawned)
        check(app.quit is True, "and the app closes so ES-DE can restart")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_page_failure_is_told_and_nothing_is_offered():
    app, ls, d = make_app()
    try:
        ls._run = lambda argv, timeout: (1, "", "reading\nNO SCRAPED GAMES YET - SCRAPE FIRST\n")
        ls.runner = ls._run
        open_page(app)
        choose(app, "REBUILD")
        app.run_pending()
        check(app.toast == ("err", "NO SCRAPED GAMES YET - SCRAPE FIRST"), "the error is shown as it is: %s" % (app.toast,))
        check(app.top["id"] == "lists" and not ls.spawned and app.quit is False, "no reload offered after a failure")
        ls.runner = lambda argv, timeout: (0, "garbage", "")
        choose(app, "REBUILD")
        app.run_pending()
        check(app.toast[0] == "err", "BROKEN: a child that answers nothing is an error")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_page_size_and_stars_are_saved_for_the_rebuild():
    app, ls, d = make_app()
    try:
        open_page(app)
        choose(app, "MUST PLAY SIZE")
        names = [lab for lab, _w in app.top["items"]]
        check(names[:4] == ["10 PER SYSTEM", "15 PER SYSTEM", "25 PER SYSTEM   (NOW)", "50 PER SYSTEM"], "the size choices: %s" % names)
        app.top["sel"] = 3
        app.handle("ok")
        app.run_pending()
        check(app.top["id"] == "lists" and ls.sizes() == (50, 4.5), "50 saved: %s" % (ls.sizes(),))
        check(L.read_state(ls.state)["must_play_top"] == 50, "in the state file the rebuild reads")
        choose(app, "MUST PLAY GENRES")
        names = [lab for lab, _w in app.top["items"]]
        check(names[:3] == ["4.0 STARS AND UP", "4.5 STARS AND UP   (NOW)", "5.0 STARS AND UP"], "the stars choices: %s" % names)
        app.top["sel"] = 2
        app.handle("ok")
        app.run_pending()
        check(ls.sizes() == (50, 5.0) and "MUST PLAY SIZE:  50" in [lab for lab, _w in app.top["items"]][1], "5.0 saved and the row shows 50")
        check(ls.set_size(7)[0] is False and ls.set_stars(3.3)[0] is False, "BROKEN: sizes and stars outside the choices are refused")
        check(ls.sizes() == (50, 5.0), "and change nothing")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_BROKEN_personal_edition_refuses_in_the_page():
    app, ls, d = make_app(edition="personal")
    try:
        open_page(app)
        labels = [lab for lab, _w in app.top["items"]]
        check(labels == ["REBUILD  (MANAGED FROM THE MAC)", "BACK"], "BROKEN: the personal edition shows no sizes and a refusing row: %s" % labels)
        choose(app, "REBUILD")
        check(app.pending is None and ls.runs == [] and app.toast[0] == "err" and "MANAGED FROM THE MAC" in app.toast[1],
              "BROKEN: choosing it refuses and runs nothing: %s" % (app.toast,))
        check(ls.rebuild()[0] is False and ls.runs == [], "BROKEN: Lists.rebuild() itself refuses")
        check(ls.reload()[0] is False and ls.spawned == [], "BROKEN: and so does the reload")
        check("MANAGED FROM THE MAC" in app.view()["cards"][0]["value"], "the card says who manages it")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reload_item_asks_first():
    app, ls, d = make_app()
    try:
        open_page(app)
        choose(app, "RELOAD ES-DE")
        check(app.top["id"] == "confirm" and ls.spawned == [], "RELOAD ES-DE asks before it does anything")
        app.top["sel"] = 1
        app.handle("ok")
        check(ls.spawned == [] and app.quit is False, "NO does nothing")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_rebuild_child_is_the_real_script_not_a_shell():
    d = tempfile.mkdtemp(prefix="hk-listsapp-")
    try:
        ls = core.Lists("/x/settings.hks", os.path.join(d, "s.json"), runner=lambda argv, t: (0, "{}", ""), spawn=lambda a: None)
        argv = []
        ls.runner = lambda a, t: argv.append(a) or (0, '{"games": 1}', "")
        ls.cfg = lambda: dict(edition="community")
        ok, msg, res = ls.rebuild()
        check(ok and argv[0][0] == sys.executable and argv[0][1].endswith("lists_rebuild.py") and argv[0][2:] == ["rebuild", "/x/settings.hks"],
              "argv list, no shell: %s" % argv)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main(globals())
