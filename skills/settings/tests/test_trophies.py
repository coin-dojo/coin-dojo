#!/usr/bin/env python3
"""Tests for the TROPHIES page (#176): trophies_core.py (data, cache, key handling, images) and a PNG render.

No network: the API answers come from fixtures (tests/fixtures/ra_*.json, made-up user and values) through an
injected getter. Broken cases: an API error must keep the cached page; malformed JSON must not crash; the web API
key must never reach an error message, the cache, the PNG preview's output or the process output; images only from
the RetroAchievements media host, PNG only, size-capped.

    python3 skills/settings/tests/test_trophies.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
sys.path.insert(0, APPDIR)
sys.path.insert(0, os.path.join(APPDIR, "..", "..", "tests", "lib"))
import trophies_core as core  # noqa: E402
from hktest import check, main, skip  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
SUMMARY = open(os.path.join(FIX, "ra_summary.json"), "rb").read()
AOTW = open(os.path.join(FIX, "ra_aotw.json"), "rb").read()
KEY = "TESTKEY" + "x" * 25            # obviously fake, the shape of a web API key (alphanumeric, 32)


def getter(summary=SUMMARY, aotw=AOTW, calls=None):
    def get(url):
        if calls is not None:
            calls.append(url)
        if isinstance(summary, Exception) and "UserSummary" in url:
            raise summary
        return aotw if "AchievementOfTheWeek" in url else summary
    return get


def page(tmp, get, key=KEY, user="ArcadePlayer"):
    kf = os.path.join(tmp, "key.json")
    if key is not None:
        with open(kf, "w") as fh:
            json.dump({"web_api_key": key}, fh)
    cfg = {"user": user, "key_file": kf, "cache_dir": os.path.join(tmp, "cache")}
    return core.Page(cfg, get=get, now=lambda: 1795000000.0)


def test_parse_fixture():
    m = core.model(json.loads(SUMMARY), json.loads(AOTW))
    p = m["profile"]
    check(p["user"] == "ArcadePlayer" and p["points"] == 1840 and p["softcore"] == 615 and p["rank"] == 48213,
          "profile: user, hardcore + softcore points, rank")
    check(len(m["recent"]) == core.RECENT_N and m["recent"][0]["title"] == "Polarity Shift",
          "recent unlocks: newest first, capped at %d" % core.RECENT_N)
    check([g["title"] for g in m["played"]][:2] == ["Ikaruga", "Crash Bandicoot"] and m["played"][0]["got"] == 5
          and m["played"][0]["total"] == 32, "recently played: progress from Awarded (5 / 32)")
    check(m["aotw"]["title"] == "Speed Demon" and m["aotw"]["game"] == "Sonic the Hedgehog", "achievement of the week")


def test_broken_malformed_answers_do_not_crash():
    for doc in (None, [], {"User": 5, "RecentAchievements": [], "RecentlyPlayed": {}, "Awarded": "x"},
                {"RecentAchievements": {"1": {"2": "nope"}}, "RecentlyPlayed": [None, 3]}):
        m = core.parse_summary(doc)
        check(isinstance(m["recent"], list) and isinstance(m["played"], list), "BROKEN input %r parses to empty lists" % (doc,))
    check(core.parse_aotw({"Achievement": "x"}) is None and core.parse_aotw(None) is None, "BROKEN AotW: None")


def test_refresh_saves_cache_and_keeps_it_offline():
    with tempfile.TemporaryDirectory() as t:
        pg = page(t, getter())
        pg.refresh()
        check(pg.state == "ok" and pg.data["profile"]["user"] == "ArcadePlayer", "refresh: state ok, data loaded")
        pg2 = page(t, getter(summary=core.FetchError("no connection to RetroAchievements (URLError)")))
        check(pg2.data and pg2.data["profile"]["points"] == 1840, "a new page shows the cache at once")
        pg2.refresh()
        check(pg2.state == "offline" and pg2.data["profile"]["points"] == 1840 and "OFFLINE" in pg2.view()["status"],
              "BROKEN network: the cached page stays, status says OFFLINE")
        pg3 = page(t, getter(summary=b"<html>maintenance</html>"))
        pg3.refresh()
        check(pg3.state == "offline" and pg3.data["profile"]["points"] == 1840, "BROKEN answer (not JSON): cache kept")
        pg4 = page(t, getter(summary=b'{"Error": "invalid key"}'))
        pg4.refresh()
        check(pg4.state == "offline" and "wrong user or key" in pg4.error, "BROKEN key: a clear error, cache kept")


def test_key_never_leaks():
    with tempfile.TemporaryDirectory() as t:
        calls = []
        pg = page(t, getter(calls=calls))
        pg.refresh()
        check(calls and all(KEY in c for c in calls), "the key goes only into the API request URLs")
        cache = open(core.cache_path(os.path.join(t, "cache"))).read()
        check(KEY not in cache, "the key is not in the cache")
        check(KEY not in json.dumps(pg.view(), default=str), "the key is not in the page view")
        err = {}

        def failing(url):
            err["url"] = url
            raise core.FetchError("RetroAchievements answered HTTP 401")
        pg = page(t, failing)
        pg.refresh()
        check(KEY in err["url"] and KEY not in pg.error and "http" not in pg.error.lower().replace("http 401", ""),
              "an error message never carries the URL or the key: %r" % pg.error)
    try:
        core.http_get("http://127.0.0.1:9/API/x.php?y=" + KEY, timeout=1)
    except core.FetchError as e:
        check(KEY not in str(e), "http_get's own errors never carry the URL: %r" % str(e))


def test_missing_key_screen():
    with tempfile.TemporaryDirectory() as t:
        pg = page(t, getter(), key=None)
        check(pg.state == "nokey" and "NO RETROACHIEVEMENTS KEY" in pg.view()["status"], "no key file: the no-key screen")
        calls = []
        pg = page(t, getter(calls=calls), key="bad key with spaces")
        pg.refresh()
        check(pg.state == "nokey" and not calls, "BROKEN key file (bad characters): treated as missing, nothing fetched")


def test_images_only_from_the_media_host():
    check(core.image_url("badge", "100001") == "https://media.retroachievements.org/Badge/100001.png", "badge URL")
    check(core.image_url("icon", "/Images/000010.png").startswith("https://media.retroachievements.org/Images/"), "icon URL")
    for bad in ("../../etc/passwd", "100001.png?x=1", "/Images/../../x.png", "https://evil.example/x.png", "/Badge/x.png"):
        check(core.image_url("icon", bad) == "" and core.image_url("badge", bad) == "", "BROKEN image name %r refused" % bad)
    with tempfile.TemporaryDirectory() as t:
        png = core.PNG_MAGIC + b"\0" * 64
        url = core.image_url("badge", "100001")
        path = core.fetch_image(t, url, get=lambda u: png)
        check(path and open(path, "rb").read() == png and path.startswith(t), "a PNG lands in the cache folder")
        check(core.fetch_image(t, core.image_url("badge", "100002"), get=lambda u: b"<html>") == "", "BROKEN: not a PNG, not cached")
        check(core.fetch_image(t, core.image_url("badge", "100003"), get=lambda u: core.PNG_MAGIC + b"\0" * core.MAX_IMAGE_BYTES)
              == "", "BROKEN: an oversized image is refused")
        check(core.fetch_image(t, "https://evil.example/Badge/1.png", get=lambda u: png) == "", "BROKEN: another host is refused")


def test_input():
    with tempfile.TemporaryDirectory() as t:
        pg = page(t, getter())
        pg.refresh()
        for a in ("down", "down", "down", "down", "down", "down", "down"):
            pg.handle(a)
        check(pg.sel[0] == core.RECENT_N - 1, "down stops at the last row")
        pg.handle("right")
        pg.handle("down")
        check(pg.focus == 1 and pg.sel == [core.RECENT_N - 1, 1], "right picks RECENTLY PLAYED; its own cursor")
        pg.handle("back")
        check(pg.quit, "MK (back) leaves the page")


def test_png_render():
    out = tempfile.mkdtemp(prefix="trophies-test-")
    app = os.path.join(APPDIR, "trophies_app.py")
    fx = "%s,%s" % (os.path.join(FIX, "ra_summary.json"), os.path.join(FIX, "ra_aotw.json"))
    r = subprocess.run([sys.executable, app, "--png", os.path.join(out, "page.png"), "--fixture", fx, "--now", "1795000000"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and ("library not found" in r.stderr or "cairo" in r.stderr.lower()):
        skip("png render: no cairo/freetype here (%s)" % r.stderr.strip()[-120:])
        return
    p = os.path.join(out, "page.png")
    check(r.returncode == 0 and os.path.getsize(p) > 20000, "the page renders to a PNG (rc=%d %s)" % (r.returncode, r.stderr[-200:]))
    check(open(p, "rb").read(8) == core.PNG_MAGIC, "the output is a PNG")
    r = subprocess.run([sys.executable, app, "--png", os.path.join(out, "nokey.png"), "--entry", "/nonexistent"],
                       capture_output=True, text=True, timeout=60)
    check(r.returncode == 0 and os.path.isfile(os.path.join(out, "nokey.png")), "the no-key screen renders")
    check(not os.path.exists(os.path.expanduser(core.DEFAULT_CACHE + "/trophies.json")) or
          os.path.getmtime(os.path.expanduser(core.DEFAULT_CACHE + "/trophies.json")) < os.path.getmtime(p),
          "a preview never writes the real cache")


# ------------------------------------------------------------------------------ #196: the user logged in via SETTINGS
import settings_core as sc  # noqa: E402
import urllib.parse  # noqa: E402

TOK = "FAKEtoken0123456"            # obviously fake


def echo_getter(calls):
    """The fixture summary, but for whichever user was asked (u=...)."""
    def get(url):
        calls.append(url)
        if "AchievementOfTheWeek" in url:
            return AOTW
        u = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["u"][0]
        return json.dumps(dict(json.loads(SUMMARY), User=u)).encode()
    return get


class Home:
    """A cabinet home: the app's state file + retroarch.cfg + emu.cfg, and a TROPHIES cfg pointing at them."""

    def __init__(self, t):
        self.t = t
        self.state = os.path.join(t, "coin-dojo", "settings-state.json")
        self.ra, self.emu = os.path.join(t, "retroarch.cfg"), os.path.join(t, "emu.cfg")
        kf = os.path.join(t, "key.json")
        with open(kf, "w") as fh:
            json.dump({"web_api_key": KEY}, fh)
        self.cfg = {"user": "DeployUser", "key_file": kf, "cache_dir": os.path.join(t, "cache"),
                    "settings_state": self.state, "cheevos_files": {"retroarch": self.ra, "flycast": self.emu}}

    def write(self, state=None, ra=None, emu=None):
        for path, text in ((self.state, state), (self.ra, ra), (self.emu, emu)):
            if text is None:
                if os.path.exists(path):
                    os.remove(path)
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)

    def user_asked(self, cfg=None):
        calls = []
        pg = core.Page(dict(cfg or self.cfg), get=echo_getter(calls), now=lambda: 1795000000.0)
        pg.refresh()
        asked = [urllib.parse.parse_qs(urllib.parse.urlsplit(c).query)["u"][0] for c in calls if "UserSummary" in c]
        return pg, asked


RA_IN = 'video_driver = "gl"\ncheevos_username = "AppUser"\ncheevos_token = "%s"\n' % TOK
EMU_IN = "[achievements]\nUserName = FlyUser\nToken = %s\n" % TOK


def test_follows_the_app_login():
    with tempfile.TemporaryDirectory() as t:
        h = Home(t)
        h.write('{"benchmark_mode": true, "cheevos_login": true}', RA_IN, EMU_IN)
        pg, asked = h.user_asked()
        check(asked == ["AppUser"] and pg.view()["user"] == "AppUser" and pg.data["profile"]["user"] == "AppUser",
              "logged in via SETTINGS: TROPHIES asks RA for the app's user (retroarch.cfg): %r" % asked)
        check(TOK not in json.dumps(pg.view(), default=str) and TOK not in repr(pg.cfg) and TOK not in pg.user,
              "the RA login token is never read into the page")
        h.write('{"cheevos_login": true}', 'cheevos_username = ""\n', EMU_IN)
        check(h.user_asked()[1] == ["FlyUser"], "no user in retroarch.cfg: emu.cfg [achievements] UserName")
        with open(os.path.join(t, "trophies.hks"), "w") as fh:
            json.dump({"user": "DeployUser", "key_file": h.cfg["key_file"], "cache_dir": "~/c",
                       "settings_state": "~/s.json", "cheevos_files": {"retroarch": "~/r.cfg", "bogus": "/etc/x", "flycast": 5}}, fh)
        c = core.load_config(os.path.join(t, "trophies.hks"))
        check(c["settings_state"] == os.path.expanduser("~/s.json") and c["cheevos_files"] == {"retroarch": os.path.expanduser("~/r.cfg")},
              "the entry file's settings_state + cheevos_files are read (only retroarch/flycast string paths): %r" % c)


def test_falls_back_to_hk_cheevos_user():
    with tempfile.TemporaryDirectory() as t:
        h = Home(t)
        cases = [("no state file", None, RA_IN, EMU_IN),
                 ("logged out in SETTINGS", '{"cheevos_login": false}', RA_IN, EMU_IN),
                 ("the app never logged in", '{"benchmark_mode": true}', RA_IN, EMU_IN),
                 ("a string, not true", '{"cheevos_login": "true"}', RA_IN, EMU_IN),
                 ("1, not true", '{"cheevos_login": 1}', RA_IN, EMU_IN),
                 ("malformed state", "{cheevos_login: true", RA_IN, EMU_IN),
                 ("a list", "[true]", RA_IN, EMU_IN),
                 ("logged in but no user in either cfg", '{"cheevos_login": true}', 'cheevos_username = ""\n', None),
                 ("a user name RA would never give", '{"cheevos_login": true}', 'cheevos_username = "x y;rm"\n',
                  "[achievements]\nUserName = ../etc\n")]
        for why, st, ra, emu in cases:
            h.write(st, ra, emu)
            pg, asked = h.user_asked()
            check(asked == ["DeployUser"] and pg.view()["user"] == "DeployUser",
                  "BROKEN CASE: %s -> hk_cheevos_user (%r)" % (why, asked))
        h.write('{"cheevos_login": true}', RA_IN, EMU_IN)
        cfg = dict(h.cfg)
        del cfg["settings_state"], cfg["cheevos_files"]
        check(h.user_asked(cfg)[1] == ["DeployUser"], "an entry without the app's paths (old deploy): hk_cheevos_user")
        cfg = dict(h.cfg, user="")
        h.write(None, RA_IN, EMU_IN)
        pg, asked = h.user_asked(cfg)
        check(pg.state == "nokey" and not asked, "no app login and no hk_cheevos_user: the connect screen, nothing fetched")


def test_follows_a_login_made_while_it_runs():
    """The overlay (trophiesd) keeps one Page: a login/logout in SETTINGS counts on its next refresh, and the cached
    page of the old user is not shown as the new one's."""
    with tempfile.TemporaryDirectory() as t:
        h = Home(t)
        h.write(None, None, None)
        calls = []
        pg = core.Page(h.cfg, get=echo_getter(calls), now=lambda: 1795000000.0)
        pg.refresh()
        check(pg.user == "DeployUser" and pg.data["profile"]["user"] == "DeployUser", "before: the deploy's user")
        ch = sc.Cheevos(dict(retroarch=h.ra, flycast=h.emu), h.state, busy=lambda: False,
                        login=lambda api, u, p: ("AppUser", TOK))
        ok, _m = ch.login("appuser", "not-a-real-password")
        check(ok, "the SETTINGS app logs in (fake login, its real file writes)")
        pg.get = lambda url: (_ for _ in ()).throw(core.FetchError("no connection to RetroAchievements (URLError)"))
        pg.refresh()
        check(pg.user == "AppUser" and pg.data is None and pg.state == "offline" and pg.view()["user"] == "AppUser",
              "after the login, offline: the new user, and NOT the old user's cached page")
        pg.get = echo_getter(calls)
        pg.refresh()
        check(pg.state == "ok" and pg.data["profile"]["user"] == "AppUser", "online again: the app user's page")
        ok, _m = ch.logout()
        pg.refresh()
        check(ok and pg.user == "DeployUser" and pg.data["profile"]["user"] == "DeployUser",
              "LOG OUT in SETTINGS: back to hk_cheevos_user")
        fresh = core.Page(dict(h.cfg, user="Someone_Else"), get=echo_getter([]))
        check(fresh.data is None, "BROKEN CASE: a cache of another user is not shown at start")
        fresh = core.Page(dict(h.cfg, user="deployuser"), get=echo_getter([]))
        check(fresh.data and fresh.data["profile"]["user"] == "DeployUser", "the same user in another case keeps the cache")


if __name__ == "__main__":
    main(globals())
