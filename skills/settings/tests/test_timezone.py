#!/usr/bin/env python3
"""TIME ZONE page of the SETTINGS app (#206): the validation in settings_core.TimeZone and the page's screens.

    python3 skills/settings/tests/test_timezone.py

The runner is a fake `timedatectl` (argv recorded, no process), the zoneinfo tree a temp dir: nothing touches the host.
Broken cases (each must be refused BEFORE anything runs): an invalid zone, path traversal (`../`, `a/../b`, a leading
`/`), a second argument smuggled into the name, an option-shaped name, a trailing newline, a zone on timedatectl's list
without a zoneinfo file and the other way round. Also: no state file -> the app still reads the zone (config applies it
at deploy), a failing timedatectl changes nothing and writes no state, an unwritable state file is told.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
import settings_core as core  # noqa: E402
from hktest import check, main  # noqa: E402

ZONES = ["America/New_York", "America/Chicago", "America/Argentina/Buenos_Aires", "Europe/London", "Europe/Paris",
         "Asia/Tokyo", "Etc/UTC", "Australia/Sydney", "America/Sao_Paulo", "America/Denver", "America/Los_Angeles"]


class Env:
    def __init__(self, listed=None, files=None, current="Europe/London", rc=0, state=True):
        self.dir = tempfile.mkdtemp(prefix="hk-tz-")
        self.tzdir = os.path.join(self.dir, "zoneinfo")
        for z in (ZONES if files is None else files):
            p = os.path.join(self.tzdir, z)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").close()
        os.makedirs(os.path.join(self.tzdir, "posix", "Europe"))
        open(os.path.join(self.tzdir, "posix", "Europe", "London"), "wb").close()      # not a zone name
        open(os.path.join(self.tzdir, "tzdata.zi"), "wb").close()
        self.listed = ZONES if listed is None else listed
        self.current, self.rc, self.calls = current, rc, []
        self.state = os.path.join(self.dir, "cfg", "settings-state.json") if state else "/proc/nope/state.json"
        self.tz = core.TimeZone(runner=self.run, log=core.Log(None), state=self.state, tzdir=self.tzdir,
                                tool="timedatectl", localtime=os.path.join(self.dir, "no-localtime"))

    def run(self, argv, timeout):
        self.calls.append(argv)
        if argv[1:] == ["show", "-p", "Timezone", "--value"]:
            return 0, self.current + "\n", ""
        if argv[1:] == ["list-timezones"]:
            return 0, "\n".join(self.listed) + "\n", ""
        if argv[1] == "set-timezone":
            if self.rc == 0:
                self.current = argv[2]
            return self.rc, "", "" if self.rc == 0 else "Failed to set time zone: Interactive authentication required.\n"
        return 127, "", "?"

    def sets(self):
        return [c for c in self.calls if c[1] == "set-timezone"]

    def saved(self):
        try:
            with open(self.state, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return None

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def test_set_writes_state_and_runs_one_argument():
    e = Env()
    try:
        ok, msg = e.tz.set("America/New_York")
        check(ok and "NEW YORK" in msg, "a listed zone is set: %s" % msg)
        check(e.sets() == [["timedatectl", "set-timezone", "America/New_York"]], "exactly `timedatectl set-timezone ZONE`: %s" % e.sets())
        check(e.saved() == {"timezone": "America/New_York"}, "the zone is remembered in the state file (cabinet wins)")
        check(e.tz.current() == "America/New_York", "current() follows")
        st = os.stat(e.state).st_mode & 0o777
        check(st == 0o600, "state file is 0600 (%o)" % st)
    finally:
        e.close()


def test_state_keeps_other_keys():
    e = Env()
    try:
        os.makedirs(os.path.dirname(e.state))
        with open(e.state, "w", encoding="utf-8") as f:
            json.dump({"benchmark_mode": True, "cheevos_login": False}, f)
        e.tz.set("Asia/Tokyo")
        check(e.saved() == {"benchmark_mode": True, "cheevos_login": False, "timezone": "Asia/Tokyo"},
              "other keys of the state file are kept: %s" % e.saved())
    finally:
        e.close()


def test_BROKEN_bad_zones_never_reach_timedatectl():
    e = Env()
    try:
        bad = ["Mars/Olympus_Mons",                                   # well-formed, not a zone
               "../../etc/passwd", "America/../../etc/passwd", "America/../New_York", "/usr/share/zoneinfo/Europe/London",
               "/etc/passwd", "..", "America/./New_York", "America//New_York", "America/New_York/",
               "Europe/London Asia/Tokyo", "Europe/London;reboot", "Europe/London\n", "Europe/London\nAsia/Tokyo",
               "$(reboot)", "`id`", "--help", "-h", "--no-ask-password", "", " ", "Europe/London ", "x" * 65, "A/" + "b" * 70,
               "posix/Europe/London", "tzdata.zi", "UTC", None, 5, ["Europe/London"], ("Europe/London", "Asia/Tokyo")]
        for z in bad:
            ok, msg = e.tz.set(z)
            check(not ok and msg, "BROKEN CASE: %r is refused (%s)" % (z if not isinstance(z, str) else z[:24], msg))
        check(e.sets() == [], "BROKEN CASE: nothing reached timedatectl set-timezone (%s)" % e.sets())
        check(e.saved() is None, "BROKEN CASE: nothing was written to the state file")
        check(not any(e.tz.valid(z) for z in bad), "valid() is False for every bad zone")
    finally:
        e.close()


def test_BROKEN_list_and_files_must_agree():
    e = Env(listed=ZONES + ["Europe/Atlantis"], files=ZONES)         # on the list, no zoneinfo file
    try:
        check(not e.tz.valid("Europe/Atlantis"), "BROKEN CASE: on timedatectl's list but no zoneinfo file -> refused")
    finally:
        e.close()
    e = Env(listed=[z for z in ZONES if z != "Asia/Tokyo"])        # a file, not on the list
    try:
        check(not e.tz.valid("Asia/Tokyo") and e.tz.valid("Europe/Paris"),
              "BROKEN CASE: a zoneinfo file timedatectl does not list -> refused, the rest stays valid")
        check(not e.tz.set("Asia/Tokyo")[0] and e.sets() == [], "BROKEN CASE: and never set")
    finally:
        e.close()


def test_timedatectl_failure_changes_nothing():
    e = Env(rc=1)
    try:
        ok, msg = e.tz.set("Europe/Paris")
        check(not ok and "authentication" in msg.lower(), "a failing timedatectl is reported as it said: %s" % msg)
        check(e.saved() is None and e.tz.current() == "Europe/London", "BROKEN CASE: failure -> no state written, zone unchanged")
    finally:
        e.close()


def test_unwritable_state_is_told():
    e = Env(state=False)
    try:
        ok, msg = e.tz.set("Europe/Paris")
        check(ok and "NOT SAVED" in msg, "zone set but the state could not be written: told (%s)" % msg)
    finally:
        e.close()


def test_no_state_file_still_reads_the_zone():
    e = Env()
    try:
        page = core.TimeZonePage(e.tz)
        check(e.saved() is None and e.tz.current() == "Europe/London", "no state file: the app shows the system zone")
        check("LONDON" in page.label() and page.cards()[0]["value"] == "EUROPE / LONDON", "label and card show it")
        check(any(w == "z:Europe/London" and "(NOW)" in lab for lab, w in page.items()), "the preset list marks it")
    finally:
        e.close()


def test_regions_and_cities_come_from_the_validated_list():
    e = Env()
    try:
        check(e.tz.regions() == ["America", "Asia", "Australia", "Europe"], "regions: %s (no Etc, no posix)" % e.tz.regions())
        check(("ARGENTINA / BUENOS AIRES", "America/Argentina/Buenos_Aires") in e.tz.cities("America"), "a nested city is kept")
        check(e.tz.valid("America/Argentina/Buenos_Aires"), "a three-part zone is valid")
        check(all(e.tz.valid(z) for _l, z in core.TZ_PRESETS), "every preset of the short list is valid on this fake cabinet")
    finally:
        e.close()


def test_presets_cover_the_ticket():
    zs = [z for _l, z in core.TZ_PRESETS]
    for need in ("America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Etc/UTC", "Europe/London",
                 "Asia/Tokyo", "Australia/Sydney", "America/Sao_Paulo"):
        check(need in zs, "short list has %s" % need)
    check(any(z in zs for z in ("Europe/Paris", "Europe/Berlin")), "short list has Paris / Berlin")
    check(all(core.tz_error(z) is None for z in zs), "presets pass the name shape check")


def test_screens_more_region_city_set():
    e = Env()
    try:
        a = core.App(core.NM("/bin/false", log=core.Log(None)), type("H", (), {"current": lambda s: "h"})(), core.Log(None),
                     None, [core.TimeZonePage(e.tz)])
        a.pending = None
        home = [lab for lab, _w in a.top["items"]]
        check(any(lab.startswith("TIME ZONE:") for lab in home), "home lists TIME ZONE: %s" % home)
        a.stack[0]["sel"] = [i for i, (lab, _w) in enumerate(a.top["items"]) if lab.startswith("TIME ZONE")][0]
        a.handle("ok")
        check(a.top["id"] == "tz" and a.view()["cards"], "TIME ZONE page open with its status card")
        a.top["sel"] = [i for i, (lab, _w) in enumerate(a.top["items"]) if lab.startswith("MORE")][0]
        a.handle("ok")
        check(a.top["id"] == "tz_regions" and a.view()["cards"] == [], "MORE opens the regions (no status cards)")
        names = [lab for lab, _w in a.top["items"]]
        a.top["sel"] = names.index("ASIA")
        a.handle("ok")
        check(a.top["id"] == "tz_cities" and ("TOKYO", "z:Asia/Tokyo") in [(l, w) for l, w in a.top["items"]], "region -> its cities")
        a.top["sel"] = [w for _l, w in a.top["items"]].index("z:Asia/Tokyo")
        a.handle("ok")
        check(a.pending is not None, "choosing a city queues the write (busy screen)")
        a.run_pending()
        check(a.top["id"] == "tz" and a.toast[0] == "ok" and "TOKYO" in a.toast[1], "back on the page with the result: %s" % (a.toast,))
        check(e.sets() == [["timedatectl", "set-timezone", "Asia/Tokyo"]], "one set-timezone call: %s" % e.sets())
        a.handle("back")
        check(a.top["id"] == "home" and any("TOKYO" in lab for lab, _w in a.top["items"]), "home label shows the new zone")
    finally:
        e.close()


if __name__ == "__main__":
    main(globals())
