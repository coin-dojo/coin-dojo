#!/usr/bin/env python3
"""Tests for `make smoke-launch` (#48): tests/smoke/{smoke_launch.py,smoke_remote.sh,smoke_games.tsv}, mk/smoke.mk.

Run: python3 tests/smoke/test_smoke_launch.py   (stdlib only; bash + GNU make)

Nothing here starts a real emulator, grim or ES-DE: the target is "local", launch.py is the real one but
COIN_DOJO_RETROARCH / COIN_DOJO_FLYCAST / COIN_DOJO_CORES point at stub scripts, and pgrep / pkill / grim
are stubs first on PATH (the stub pkill only logs, so a test can never kill a real process).

Covers: the pinned table (well-formed, every launch_expected.json system, both ways); the uniform-frame
and frozen-frame detectors; the verdict (judge); and the target-side script end to end: a good run PASSes
and RetroArch got `--appendconfig` whose FIRST line is config_save_on_exit = "false".
Env: SMOKE_TEST_WAIT / SMOKE_TEST_GAP / SMOKE_TEST_SETTLE (default 1 / 1 / 0.1 s) tune the sleeps of every stub run.
Also covered: never killing by name (a game that appears mid-run is left alive, exit 9, no config restored, pkill.log
absent), the process-group kill of a flycast that forks (flycast + its watcher), the wrapper marker, the EXIT trap
on SIGTERM, prep refusing an interrupted run's changed config, and finish deleting the target's scratch dirs.
Attract mode (#104): ScreensaverTimer is 0 at every launch and the ORIGINAL value (1234567 here, not a hardcoded one) is back after a good
run, a FAIL, Ctrl-C / SIGTERM / SIGHUP of the controller and a remote-step drop; refused while a game runs; a foreign game: no restart and the
exact restore command printed; getty@tty1 not active, a missing line and a next run after an unrestored one are loud; no file other than
es_settings.xml ever holds its (fake) password.
Broken cases (must FAIL): a table with a missing / duplicate / odd row; an emulator that is dead at 10 s;
an all-black and an all-grey screenshot; a frozen picture; a stub RetroArch that rewrites retroarch.cfg
and a stub flycast that rewrites emu.cfg (both detected AND restored); a missing core; a game already
running (refused, nothing launched); ES-DE not up (refused); `make smoke-launch LIMIT=bogus`.
Load (#245): every stub says it is up (HK_READY_FILE) before its last line and the target step's wait clock starts then,
so a slow launch under a loaded `make test` is not judged as the emulator's doing; waits on processes are bounded polls,
never a fixed sleep. Broken: a 3 s slow start whose config write the old clock missed; a never-ready stub (bounded).
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import smoke_launch as sl  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, finish  # noqa: E402  (shared helper, #137)

LAUNCH = os.path.join(ROOT, "skills", "collections", "launch.py")
T_WAIT = os.environ.get("SMOKE_TEST_WAIT", "1")
T_GAP = os.environ.get("SMOKE_TEST_GAP", "1")
T_SETTLE = os.environ.get("SMOKE_TEST_SETTLE", "0.1")




def ppm(w, h, fn):
    return b"P6\n%d %d\n255\n" % (w, h) + b"".join(bytes(fn(x, y)) for y in range(h) for x in range(w))


def frame(w, h, kind):
    """Rgb bytes of a synthetic frame: black / grey / art (not uniform) / art2 (art, shifted)."""
    if kind == "black":
        return bytes(w * h * 3)
    if kind == "grey":
        return bytes([128, 128, 128]) * (w * h)
    shift = 40 if kind == "art2" else 0
    return b"".join(bytes(((x * 7 + shift) % 256, (y * 5) % 256, ((x + y) * 3) % 256)) for y in range(h) for x in range(w))


# --- 1. the table --------------------------------------------------------------------------------------
expected = json.load(open(sl.EXPECTED))["systems"]
rows = sl.load_table()
check(sl.table_problems(rows, expected) == [], "smoke_games.tsv: well-formed and covers every launch_expected.json system, both ways (%d systems)" % len(rows))
check(len(rows) == len(expected) == 17, "table has 17 rows")
if sl.TABLE == sl.PERSONAL_TABLE:       # #190: the maintainer's table pins the games he confirmed on 2026-11-14
    check(any(r[0] == "dreamcast" and r[1] != "-" for r in rows) and any(r[0] == "n64" and "Killer Instinct" in r[1] for r in rows)
          and any(r[0] == "fbneo" and r[1] == "sf.zip" for r in rows), "personal table pins the games the maintainer confirmed")
example = sl.load_table(os.path.join(os.path.dirname(sl.HERE), "smoke", "smoke_games.tsv"))
check(sl.table_problems(example, expected) == [] and all(r[1] == "-" for r in example),
      "community example table: well-formed, covers every system, pins no game (#190)")
check(any("missing" in b for b in sl.table_problems([r for r in rows if r[0] != "nes"], expected)) or
      any("no row" in b for b in sl.table_problems([r for r in rows if r[0] != "nes"], expected)), "broken table: a system without a row is reported")
check(bool(sl.table_problems(rows + [("nes", "x.zip", "-", "10", "yes")], expected)), "broken table: a duplicate row is reported")
check(bool(sl.table_problems(rows + [("zx81", "x.zip", "-", "10", "yes")], expected)), "broken table: a system not in launch_expected.json is reported")
check(bool(sl.table_problems([("nes", "../x.zip", "-", "10", "yes")], {"nes": 1})), "broken table: a path in the ROM column is reported")
check(bool(sl.table_problems([("nes", "x.zip", "-")], {"nes": 1})), "broken table: a row with 2 columns is reported")

ok1 = ("nes", "x.zip", "-", "10", "yes")
check(sl.table_problems([ok1], {"nes": 1}) == [], "table: a row with wait 10 / motion yes is fine")
check(any("wait" in b for b in sl.table_problems([("nes", "x.zip", "-", "abc", "yes")], {"nes": 1})), "broken table: a wait that is not a number is reported")
check(any("wait" in b for b in sl.table_problems([("nes", "x.zip", "-", "0", "yes")], {"nes": 1})), "broken table: wait 0 is reported")
check(any("wait" in b for b in sl.table_problems([("nes", "x.zip", "-", "", "yes")], {"nes": 1})), "broken table: a missing (empty) wait is reported")
check(any("motion" in b for b in sl.table_problems([("nes", "x.zip", "-", "10", "maybe")], {"nes": 1})), "broken table: motion other than yes/no is reported")
bw = {r[0]: r for r in rows}
check(int(bw["psx"][3]) >= 20 and int(bw["cps3"][3]) >= 20 and bw["cps3"][4] == "no" and bw["nes"][3] == "10" and bw["nes"][4] == "yes",
      "table: psx and cps3 wait >= 20 s, cps3 motion no, the rest 10 s / yes")

# --- 2. picture detectors + the verdict ----------------------------------------------------------------
W, H = 64, 48
black, grey, art, art2 = (frame(W, H, k) for k in ("black", "grey", "art", "art2"))
check(sl.dominant_fraction(black) >= sl.UNIFORM_AT, "uniform detector: all black is uniform")
check(sl.dominant_fraction(grey) >= sl.UNIFORM_AT, "uniform detector: all grey is uniform")
check(sl.dominant_fraction(art) < sl.UNIFORM_AT, "uniform detector: a picture is not uniform")
mostly = bytearray(black)
for i in range(0, len(mostly) // 100 * 3, 3):          # ~1% of the pixels lit: a dark game, still not uniform
    mostly[i:i + 3] = b"\xff\xff\xff"
check(sl.dominant_fraction(bytes(mostly)) < sl.UNIFORM_AT, "uniform detector: a mostly black picture with 1% content is not uniform")
check(sl.changed_fraction(art, art) == 0.0 and sl.changed_fraction(art, art2) > 0.5, "frozen detector: equal frames 0%, different frames > 50% changed")
check(sl.changed_fraction(art, art[:-3]) == 1.0, "frozen detector: frames of different size count as changed")
check(sl.parse_ppm(ppm(2, 2, lambda x, y: (x, y, 9)))[:2] == (2, 2), "parse_ppm reads a binary PPM")

alive = {"result": "RUN", "state": "alive", "rc": "137"}
a, b = (W, H, art), (W, H, art2)
check(sl.judge(alive, a, b)[0] == "PASS", "judge: alive + picture + moving = PASS")
check(sl.judge(dict(alive, state="dead", died_at="10", rc="139"), a, b)[0] == "FAIL", "judge: a dead emulator at 10 s = FAIL (broken case)")
check("died at 10 s" in sl.judge(dict(alive, state="dead", died_at="10", rc="139"), None, None)[1], "judge: dead reason names the second and rc")
check(sl.judge(alive, (W, H, black), b)[0] == "FAIL", "judge: an all-black screenshot = FAIL (broken case)")
check(sl.judge(alive, (W, H, grey), b)[0] == "FAIL", "judge: an all-grey screenshot = FAIL (broken case)")
check(sl.judge(alive, a, a)[0] == "FAIL" and "frozen" in sl.judge(alive, a, a)[1], "judge: two identical shots = FAIL frozen (broken case)")
check(sl.judge(alive, a, a, motion=False)[0] == "PASS", "judge: --no-motion lets a static picture pass")
check(sl.judge(alive, None, None)[0] == "FAIL", "judge: no screenshot = FAIL")
check(sl.judge(dict(alive, cfg_changed="retroarch.cfg"), a, b)[0] == "FAIL", "judge: a changed config = FAIL")
check(sl.judge(dict(alive, leftover="123"), a, b)[0] == "FAIL", "judge: a leftover process = FAIL")
check(sl.judge({"result": "SKIP", "reason": "x"}, None, None)[0] == "SKIP", "judge: SKIP stays SKIP, never PASS")
check(sl.judge({"result": "FAIL", "reason": "core missing: /x"}, None, None) == ("FAIL", "core missing: /x"), "judge: a missing core = FAIL")

tmp = tempfile.mkdtemp(prefix="hk-smoke-test-")
try:
    png = os.path.join(tmp, "sheet.png")
    sl.contact_sheet(png, [sl.fit(W, H, art), sl.fit(W, H, black), sl.fit(W, H, art2)])
    d = open(png, "rb").read()
    check(d[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(d[16:20], "big") == sl.COLS * sl.TILE_W, "contact sheet is a PNG %d px wide" % (sl.COLS * sl.TILE_W))

    # --- 3. the target-side script end to end (local target, stubs) ----------------------------------------
    stubs = os.path.join(tmp, "stubs")
    os.makedirs(stubs)

    def script(path, text):
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    # STUB_LATE_PID: a pgrep that reports that (real, foreign) pid for `retroarch` only from the STUB_LATE_AFTER-th call on
    script(os.path.join(stubs, "pgrep"), '#!/bin/bash\nname=${@: -1}\n'
           'if [ -n "${STUB_LATE_PID:-}" ] && [ "$name" = retroarch ]; then\n'
           '  n=$(cat "$STUB_DIR/pgrep.n" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$STUB_DIR/pgrep.n"\n'
           '  [ "$n" -ge "${STUB_LATE_AFTER:-3}" ] && { echo "$STUB_LATE_PID"; exit 0; }\n'
           'fi\ncase ",${STUB_RUNNING:-}," in *",$name,"*) echo 4242; exit 0;; esac\nexit 1\n')
    # pkill: the ES-DE restart (`-9 -x AppRun`, what the deploy handler does) is logged WITH the ScreensaverTimer it sees, and a scan
    # looks for the fake ScreenScraper password in any file other than es_settings.xml (a copy); any other pkill = pkill.log.
    script(os.path.join(stubs, "pkill"), '#!/bin/bash\n'
           'if [ "$*" = "-9 -x AppRun" ]; then\n'
           '  echo "timer=$(sed -n \'s/.*ScreensaverTimer" value="\\([0-9]*\\)".*/\\1/p\' "$HK_ES_SETTINGS")" >> "$STUB_DIR/restart.log"\n'
           '  grep -rl "$FAKE_PW" "$SCAN_ROOT" 2>/dev/null | grep -v "/es_settings.xml$" >> "$STUB_DIR/copies.log"\n'
           '  touch "$HK_ESDE_LOG"; exit 0\nfi\n'
           'echo "pkill $*" >> "$STUB_DIR/pkill.log"\n')
    script(os.path.join(stubs, "systemctl"), '#!/bin/bash\necho "${STUB_GETTY:-active}"\n[ "${STUB_GETTY:-active}" = active ]\n')
    script(os.path.join(stubs, "grim"),
           '#!/bin/bash\nf=${@: -1}\nfmt=ppm; [ "$1" = -t ] && fmt=$2\n'
           # png: a PNG header whose IHDR is the cabinet's 1920x1080 times grim's -s scale (so the test can see the pulled size)
           'if [ "$fmt" = png ]; then scale=1; prev=; for a in "$@"; do [ "$prev" = -s ] && scale=$a; prev=$a; done\n'
           '  python3 -c \'import sys,struct;s=float(sys.argv[2]);sys.stdout.buffer.write(b"\\x89PNG\\r\\n\\x1a\\n"+struct.pack(">I",13)+b"IHDR"+struct.pack(">II",int(1920*s),int(1080*s)))\' "x" "$scale" > "$f"; exit 0; fi\n'
           'n=$(cat "$STUB_DIR/grim.n" 2>/dev/null || echo 0); echo $((n + 1)) > "$STUB_DIR/grim.n"\n'
           'src=$STUB_DIR/shot-${STUB_SHOT:-moving}-$((n % 2)).ppm; cp "$src" "$f"\n')
    for kind in ("black", "grey"):
        for i in (0, 1):
            open(os.path.join(tmp, "shot-%s-%d.ppm" % (kind, i)), "wb").write(ppm(W, H, lambda x, y: (0, 0, 0) if kind == "black" else (128, 128, 128)))
    for i, k in ((0, art), (1, art2)):
        open(os.path.join(tmp, "shot-moving-%d.ppm" % i), "wb").write(b"P6\n%d %d\n255\n" % (W, H) + k)
        open(os.path.join(tmp, "shot-static-%d.ppm" % i), "wb").write(b"P6\n%d %d\n255\n" % (W, H) + art)
    for n in ("black", "grey", "moving", "static"):
        for i in (0, 1):
            src = os.path.join(tmp, "shot-%s-%d.ppm" % (n, i))
            shutil.move(src, os.path.join(stubs, "shot-%s-%d.ppm" % (n, i)))
    cores = os.path.join(tmp, "cores")
    os.makedirs(cores)
    open(os.path.join(cores, "nestopia_libretro.so"), "w").close()
    roms = os.path.join(tmp, "roms")
    for s in ("nes", "dreamcast"):
        os.makedirs(os.path.join(roms, s))
        open(os.path.join(roms, s, "game one.zip" if s == "nes" else "dc game.chd"), "w").write("x")
    ra_cfg, fly_cfg = os.path.join(tmp, "retroarch.cfg"), os.path.join(tmp, "emu.cfg")
    open(ra_cfg, "w").write('video_driver = "gl"\n')
    open(fly_cfg, "w").write("[config]\nrend.Resolution = 480\n")
    small_exp = os.path.join(tmp, "expected.json")
    json.dump({"systems": {"nes": [], "dreamcast": [], "arcade": []}}, open(small_exp, "w"))
    small_tab = os.path.join(tmp, "games.tsv")
    open(small_tab, "w").write("arcade\t-\t-\t10\tyes\nnes\tgame one.zip\t-\t10\tyes\ndreamcast\tdc game.chd\t-\t10\tyes\n")
    argslog = os.path.join(tmp, "ra-args.log")
    ra_stub = os.path.join(tmp, "retroarch")
    fly_stub = os.path.join(tmp, "flycast-run.sh")
    # per-run markers: the sleep arguments carry this test's pid ("sleep 47001.<pid>"), so a parallel unrelated sleep (another
    # checkout, another copy of this test, an older copy that still greps for `sleep 31337`) is never counted as OUR leftover
    # and never kills ours (#114, #107). The base number is NOT 31337 on purpose: an older copy's `pkill -f "sleep 31337"`
    # would also match "sleep 31337.<pid>". "$" stops pid 123 matching 1234.
    PID = os.getpid()
    SLEEP = "sleep 47001.%d" % PID
    SLEEP_B = "sleep 47002.%d" % PID
    LAUNCH_SHIM = os.path.join(tmp, "launch-shim.py")
    open(LAUNCH_SHIM, "w").write(
        'import os, sys\nif "--dry-run" not in sys.argv:\n    os.environ["COIN_DOJO_RETROARCH"] = %r\n'
        'os.execv(sys.executable, [sys.executable, %r] + sys.argv[1:])\n' % (ra_stub, LAUNCH))

    FAKE_PW = "NOT-A-REAL-PASSWORD-fixture-0000"
    ES_XML = os.path.join(tmp, "es_settings.xml")
    ES_ORIG = "1234567"      # deliberately NOT the cabinet's 3600000: a hardcoded restore value would be caught
    TIMER_SH = 'echo "timer=$(sed -n \'s/.*ScreensaverTimer" value="\\([0-9]*\\)".*/\\1/p\' "$HK_ES_SETTINGS")" >> "$STUB_DIR/launch-timer.log"\n'
    # #245: the stub says it is up (HK_READY_FILE) right before its last line (the exec / exit / wait), after whatever it
    # does at start; smoke_remote.sh `one` starts its wait clock then, so a slow launch under load is not the stub's fault.
    READY_FILE = os.path.join(stubs, "ready")
    READY_SH = '[ -z "${HK_READY_FILE:-}" ] || : > "$HK_READY_FILE"\n'

    def ready_body(body):
        lines = body.rstrip("\n").split("\n")
        return "\n".join(lines[:-1] + [READY_SH.rstrip("\n"), lines[-1]]) + "\n"

    def until(pred, secs=60):
        """Poll pred every 0.1 s for up to secs (generous: a loaded machine is slow, a broken one stays False). -> pred()"""
        import time as _tt
        end = _tt.time() + secs
        while not pred() and _tt.time() < end:
            _tt.sleep(0.1)
        return pred()

    def write_es(timer=ES_ORIG):
        """timer None = the file has no ScreensaverTimer line."""
        line = '<int name="ScreensaverTimer" value="%s" />\n' % timer if timer is not None else ""
        open(ES_XML, "w").write('<?xml version="1.0"?>\n<string name="ScraperPassword" value="%s" />\n%s<bool name="Other" value="true" />\n' % (FAKE_PW, line))

    def es_timer():
        import re
        m = re.search(r'name="ScreensaverTimer" value="(\d*)"', open(ES_XML).read())
        return m.group(1) if m else None

    def slog(name):
        f = os.path.join(stubs, name)
        return open(f).read().split() if os.path.exists(f) else []

    def copies_of_secret():
        """Files under tmp (stubs, scratch, backups, results) that hold the fake password, apart from es_settings.xml itself."""
        hits = []
        for root, _d, files in os.walk(tmp):
            for fn in files:
                path = os.path.join(root, fn)
                try:
                    if FAKE_PW.encode() in open(path, "rb").read() and path != ES_XML:
                        hits.append(path)
                except OSError:
                    pass
        return hits

    write_es()

    def sh(sha_file):
        return hashlib.sha256(open(sha_file, "rb").read()).hexdigest()

    def target_env(shot="moving", running="AppRun", launch=LAUNCH, extra=None):
        env = dict(os.environ, PATH=stubs + os.pathsep + os.environ["PATH"], STUB_DIR=stubs, STUB_SHOT=shot, STUB_RUNNING=running,
                   HK_LAUNCH=launch, HK_ROMS=roms, HK_OUT=os.path.join(tmp, "target-out"), HK_BAK=os.path.join(tmp, "target-bak"),
                   HK_RA_CFG=ra_cfg, HK_FLY_CFG=fly_cfg, HK_SETTLE=T_SETTLE,
                   HK_ES_SETTINGS=ES_XML, HK_ESDE_LOG=os.path.join(tmp, "es_log.txt"), HK_ESDE_POLL="0.1", HK_ESDE_GONE="0.1", HK_ESDE_WAIT="50",
                   HK_READY_FILE=READY_FILE,
                   FAKE_PW=FAKE_PW, SCAN_ROOT=tmp, COIN_DOJO_RETROARCH=ra_stub, COIN_DOJO_CORES=cores, COIN_DOJO_FLYCAST=fly_stub)
        env.update(extra or {})
        return env

    def run(ra_body, fly_body="exec %s\n" % SLEEP, shot="moving", running="AppRun", systems="nes,dreamcast", missing_core=False, table=small_tab,
            launch=LAUNCH, extra=None, es=ES_ORIG):
        """One smoke_launch.main on the local target with the stubs; -> (rc, output, results dir)."""
        script(ra_stub, '#!/bin/sh\n' + TIMER_SH + 'echo "$@" > "%s"\nprev=; for a in "$@"; do [ "$prev" = --appendconfig ] && cp "$a" "%s.cfg"; prev=$a; done\n%s' % (argslog, argslog, ready_body(ra_body)))
        script(fly_stub, "#!/bin/sh\n" + TIMER_SH + ready_body(fly_body))
        for f in (argslog, argslog + ".cfg"):
            if os.path.exists(f):
                os.unlink(f)
        core = os.path.join(cores, "nestopia_libretro.so")
        if missing_core and os.path.exists(core):
            os.unlink(core)
        elif not missing_core and not os.path.exists(core):
            open(core, "w").close()
        env = target_env(shot, running, launch, extra)
        open(os.path.join(stubs, "grim.n"), "w").write("0")
        if es != "keep":
            write_es(es)
        for f in ("pgrep.n", "pkill.log", "restart.log", "copies.log", "launch-timer.log"):
            if os.path.exists(os.path.join(stubs, f)):
                os.unlink(os.path.join(stubs, f))
        out_root = os.path.join(tmp, "results")
        buf = io.StringIO()
        old = os.environ.copy()
        os.environ.clear()
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(buf):
                rc = sl.main(["--target", "local", "--systems", systems, "--table", table, "--expected", small_exp,
                              "--out", out_root, "--wait", T_WAIT, "--gap", T_GAP])
        finally:
            os.environ.clear()
            os.environ.update(old)
        return rc, buf.getvalue(), out_root

    def leftovers():
        return subprocess.run(["pgrep", "-f", SLEEP + "$"], stdout=subprocess.PIPE).stdout.strip() != b""

    # good run: PASS, RetroArch got the appendconfig whose first line switches saving off, configs untouched
    ra0, fly0 = sh(ra_cfg), sh(fly_cfg)
    rc, out, res = run("exec %s\n" % SLEEP)
    check(rc == 0 and "RESULT: PASS nes" in out and "RESULT: PASS dreamcast" in out and "RESULT: PASS smoke-launch local" in out,
          "good run: nes + dreamcast PASS, overall PASS, exit 0")
    check("RESULT: SKIP" not in out, "good run: only the systems asked for are run")
    args = open(argslog).read() if os.path.exists(argslog) else ""
    ap = args.split("--appendconfig ")[1].split()[0] if "--appendconfig" in args else ""
    apc = open(argslog + ".cfg").read() if os.path.exists(argslog + ".cfg") else ""
    check(ap != "" and apc.splitlines()[:1] == ['config_save_on_exit = "false"'],
          "RetroArch started with --appendconfig whose FIRST line is config_save_on_exit = \"false\"")
    check('remap_save_on_exit = "false"' in apc, "the appendconfig also switches remap saving off")
    check('autosave_interval = "0"' in apc and 'history_list_enable = "false"' in apc and 'savestate_auto_save = "false"' in apc,
          "the appendconfig also switches autosave, savestate autosave and the history list off")
    check('savefile_directory = "%s' % os.path.join(tmp, "target-out") in apc and 'savestate_directory = "%s' % os.path.join(tmp, "target-out") in apc,
          "the appendconfig points savefile/savestate directories under HK_OUT")
    check("-L %s" % os.path.join(cores, "nestopia_libretro.so") in args and "game one.zip" in args, "RetroArch got the core and the ROM from launch.py (-L core rom)")
    check(sh(ra_cfg) == ra0 and sh(fly_cfg) == fly0, "retroarch.cfg and emu.cfg are byte-identical after a good run")
    check("(unchanged)" in out and "CHANGED" not in out, "the run prints both config sha256s as unchanged")
    check(not leftovers(), "no stub emulator is left running")
    stampdirs = [os.path.join(res, d) for d in os.listdir(res)]
    check(any(os.path.exists(os.path.join(d, "sheet.png")) and os.path.exists(os.path.join(d, "index.html")) and os.path.exists(os.path.join(d, "nes.png"))
              for d in stampdirs), "the run wrote sheet.png, index.html and nes.png under <out>/<stamp>/")
    check(not os.path.exists(os.path.join(tmp, "pkill.log")) and not os.path.exists(os.path.join(stubs, "pkill.log")), "a clean run never needs pkill (the group SIGKILL does it)")

    # broken: the emulator is dead at 10 s (here 1 s)
    rc, out, _ = run("exit 1\n", systems="nes")
    check(rc == 1 and ("RESULT: FAIL nes emulator died at %s s" % T_WAIT) in out and "RESULT: FAIL smoke-launch" in out, "dead emulator at the check time = FAIL, exit 1")
    check(not leftovers(), "dead emulator: nothing left running")
    # broken: all-black / all-grey / frozen screenshots
    rc, out, _ = run("exec %s\n" % SLEEP, shot="black", systems="nes")
    check(rc == 1 and "RESULT: FAIL nes screenshot is near-uniform" in out, "all-black screenshot = FAIL")
    rc, out, _ = run("exec %s\n" % SLEEP, shot="grey", systems="nes")
    check(rc == 1 and "near-uniform" in out, "all-grey screenshot = FAIL")
    rc, out, _ = run("exec %s\n" % SLEEP, shot="static", systems="nes")
    check(rc == 1 and "frozen" in out, "alive but frozen picture = FAIL")
    # broken: a RetroArch that rewrites retroarch.cfg on exit (what config_save_on_exit=true did to the cabinet)
    rc, out, _ = run('echo \'vsync = "false"\' >> "%s"\nexec %s\n' % (ra_cfg, SLEEP), systems="nes")
    check(rc == 1 and "config file changed" in out and "retroarch.cfg" in out, "a leaking RetroArch (rewrites retroarch.cfg) = FAIL")
    check(sh(ra_cfg) == ra0, "the leaked retroarch.cfg was restored from the backup")
    # broken: flycast rewrites emu.cfg
    rc, out, _ = run("exec %s\n" % SLEEP, fly_body='echo "rend.Resolution = 999" >> "%s"\nexec %s\n' % (fly_cfg, SLEEP), systems="dreamcast")
    check(rc == 1 and "emu.cfg" in out and "FAIL dreamcast" in out, "a flycast that rewrites emu.cfg = FAIL")
    check(sh(fly_cfg) == fly0, "the rewritten emu.cfg was restored (sha256 equals the one before)")
    # broken: missing core, missing ROM
    rc, out, _ = run("exec %s\n" % SLEEP, systems="nes", missing_core=True)
    check(rc == 1 and "RESULT: FAIL nes core missing" in out, "missing core = FAIL")
    os.rename(os.path.join(roms, "nes", "game one.zip"), os.path.join(roms, "nes", "moved.zip"))
    rc, out, _ = run("exec %s\n" % SLEEP, systems="nes")
    check(rc == 1 and "ROM missing" in out, "missing ROM on the cabinet = FAIL (table drift)")
    os.rename(os.path.join(roms, "nes", "moved.zip"), os.path.join(roms, "nes", "game one.zip"))
    # a system with no game pinned: loud SKIP, never PASS, run still succeeds
    rc, out, _ = run("exec %s\n" % SLEEP, systems="arcade")
    check(rc == 0 and "RESULT: SKIP arcade" in out and "PASS arcade" not in out, "'-' in the table = SKIP, never PASS")
    # per-row wait / motion from the table (no --wait override): wait 2 for nes => died at 2 s; motion no => static passes
    tab2 = os.path.join(tmp, "games2.tsv")
    open(tab2, "w").write("arcade\t-\t-\t10\tyes\nnes\tgame one.zip\t-\t2\tno\ndreamcast\tdc game.chd\t-\t1\tyes\n")

    def run_rows(ra_body, shot):
        saved = sl.main
        def patched(argv):
            i = argv.index("--wait")
            return saved(argv[:i] + argv[i + 2:])
        sl.main = patched
        try:
            return run(ra_body, shot=shot, systems="nes", table=tab2)
        finally:
            sl.main = saved
    rc, out, _ = run_rows("exit 1\n", "moving")
    # #193: under a loaded `make test` the stub's python can still be alive at the 2 s check and is then seen dead at
    # wait + gap; both prove the table's 2 s (the default wait would say "check at 10 s", "died at 10/11 s").
    died = ("died at 2 s", "died at %d s" % (2 + int(T_GAP)))
    check(any(x in out for x in died) and "check at 2 s" in out,
          "per-row wait: the table's wait (2 s) is used, not the default: " + " | ".join(
              ln for ln in out.splitlines() if "died at" in ln or "check at" in ln)[:300])
    rc, out, _ = run_rows("exec %s\n" % SLEEP, "static")
    check(rc == 0 and "RESULT: PASS nes" in out, "per-row motion no: a static picture PASSes")
    rc, out, _ = run("exec %s\n" % SLEEP, shot="static", systems="nes")
    check(rc == 1 and "frozen" in out, "per-row motion yes: the same static picture FAILs")
    check("[1/1] nes: launching" in out, "a progress line is printed before each system")
    # #245: a slow start (here 3 s, longer than wait + gap: what a loaded `make test` did to launch.py -> wrapper -> stub)
    # is judged from the stub's readiness: its config write is seen (BROKEN before #245: killed at 2 s, before the write,
    # and called a PASS), and a clean slow starter PASSes
    rc, out, _ = run('sleep 3\necho \'vsync = "false"\' >> "%s"\nexec %s\n' % (ra_cfg, SLEEP), systems="nes")
    check(rc == 1 and "config file changed" in out and sh(ra_cfg) == ra0, "SLOW START #245: a config write after a 3 s start is still caught and restored")
    rc, out, _ = run("sleep 3\nexec %s\n" % SLEEP, systems="nes")
    check(rc == 0 and "RESULT: PASS nes" in out, "SLOW START #245: a clean emulator that needs 3 s to come up PASSes")
    # the readiness wait is bounded: a stub that is never ready in time is judged as before (ready=0), not waited for forever
    for x in (os.path.join(tmp, "target-bak"), os.path.join(tmp, "target-out")):
        shutil.rmtree(x, ignore_errors=True)
    script(ra_stub, "#!/bin/sh\nsleep 5\n" + READY_SH + "exec %s\n" % SLEEP)
    envb = target_env(extra={"HK_WAIT": "1", "HK_GAP": "1", "HK_READY_POLLS": "10"})
    subprocess.run(["bash", os.path.join(HERE, "smoke_remote.sh"), "prep"], env=envb, stdout=subprocess.PIPE)
    import time as _t0
    t0 = _t0.time()
    r = subprocess.run(["bash", os.path.join(HERE, "smoke_remote.sh"), "one", "nes", "game one.zip"], env=envb, stdout=subprocess.PIPE, universal_newlines=True, timeout=120)
    took = _t0.time() - t0
    # ready=0 is the proof (unbounded, the step would have seen the file at 5 s); the time is only reported
    check("ready=0" in r.stdout and "state=alive" in r.stdout, "BROKEN readiness #245: never ready within HK_READY_POLLS = ready=0, judged without it (%.1f s)" % took)
    subprocess.run(["bash", os.path.join(HERE, "smoke_remote.sh"), "finish"], env=envb, stdout=subprocess.PIPE)
    check(until(lambda: not leftovers()), "BROKEN readiness: nothing left running")
    # guards
    rc, out, _ = run("exec %s\n" % SLEEP, running="retroarch,AppRun")
    check(rc == 9 and "refused, a game is already running" in out and not os.path.exists(argslog), "a game already running = refused (exit 9), nothing launched")
    rc, out, _ = run("exec %s\n" % SLEEP, running="flycast,AppRun")
    check(rc == 9 and not os.path.exists(os.path.join(stubs, "pkill.log")), "flycast running = refused, and nothing was killed")
    rc, out, _ = run("exec %s\n" % SLEEP, running="x")
    check(rc == 8 and "ES-DE" in out and not os.path.exists(argslog), "ES-DE not running = refused (exit 8)")
    rc, out, _ = run("exec %s\n" % SLEEP, running="es-de")
    check(rc == 0, "ES-DE under the name es-de (the VM) satisfies the guard")
    rc, out, _ = run("exec %s\n" % SLEEP, systems="bogus")
    check(rc == 2 and "unknown system" in out, "an unknown system in SYSTEMS= is a usage error (exit 2)")
    check(not leftovers(), "no stub emulator is left running after all the broken cases")

    # --- 3b. never kill by name; the group kill; the wrapper marker; scratch dirs ----------------------------
    def alive_pid(pid):
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE).stdout.strip()[:1] not in (b"", b"Z")

    def pkill_log():
        return os.path.exists(os.path.join(stubs, "pkill.log"))

    def sleepers(*cmds):
        return [c for c in cmds if subprocess.run(["pgrep", "-f", c + "$"], stdout=subprocess.PIPE).stdout.strip()]

    target_out, target_bak = os.path.join(tmp, "target-out"), os.path.join(tmp, "target-bak")
    ra0, fly0 = sh(ra_cfg), sh(fly_cfg)
    rc, out, _ = run("exec %s\n" % SLEEP, systems="nes")
    check(rc == 0 and not os.path.exists(target_out) and not os.path.exists(target_bak),
          "finish deletes the target's HK_OUT and HK_BAK after a clean run")
    check(not pkill_log(), "clean run: pkill never called")

    # a flycast that forks (flycast + watcher, as flycast-run.sh does): the group kill takes both children
    rc, out, _ = run("exec %s\n" % SLEEP, fly_body="%s &\n%s &\nwait\n" % (SLEEP, SLEEP_B), systems="dreamcast")
    check(rc == 0 and "RESULT: PASS dreamcast" in out, "forking flycast stub: PASS")
    check(sleepers(SLEEP, SLEEP_B) == [], "GROUP KILL: both children of the forking flycast (own marked sleeps) are dead after the run")
    check(not pkill_log(), "GROUP KILL: nothing needed pkill")

    # a game the maintainer starts mid-run (pgrep reports it from the 3rd retroarch lookup on): left alone, exit 9, nothing killed
    foreign = subprocess.Popen(["sleep", "31339"], start_new_session=True)
    try:
        leak_cfg = 'echo \'vsync = "false"\' >> "%s"\nexec %s\n' % (ra_cfg, SLEEP)
        rc, out, _ = run(leak_cfg, systems="nes,dreamcast", extra={"STUB_LATE_PID": str(foreign.pid), "STUB_LATE_AFTER": "4"})
        check(rc == 9 and "a game started during the run" in out and "RESULT: FAIL smoke-launch" in out, "a game started mid-run: reported, exit 9")
        check(alive_pid(foreign.pid), "NEVER BY NAME: the foreign game survived the run")
        check(not pkill_log(), "NEVER BY NAME: pkill.log is absent")
        check("[2/2]" not in out, "a game started mid-run: the run stops there")
        check(sh(ra_cfg) != ra0 and "NOT restored" in out, "foreign game seen: the changed retroarch.cfg is reported, NOT restored")
        check(os.path.isdir(target_bak), "foreign game seen: the backup is kept")
        check(not leftovers(), "foreign game seen: our own emulator group is still gone")
        check(es_timer() == "0" and slog("restart.log") == ["timer=0"], "foreign game seen: ES-DE NOT restarted again, ScreensaverTimer left at 0")
        check("attract mode was NOT put back (ScreensaverTimer was %s)" % ES_ORIG in out and ("sed -i 's/\\(name=\"ScreensaverTimer\" value=\"\\)[0-9]*\"/\\1%s\"/' " % ES_ORIG) in out
              and "pkill -9 -x AppRun" in out, "foreign game seen: the exact restore command (with the ORIGINAL value) is printed for the coordinator")
        check(os.path.exists(os.path.join(target_bak, "screensaver.orig")) and open(os.path.join(target_bak, "screensaver.orig")).read().strip() == ES_ORIG,
              "foreign game seen: the original value is kept in HK_BAK/screensaver.orig (the number only)")
        # the next run must refuse: attract mode was never put back
        rc, out, _ = run("exec %s\n" % SLEEP, systems="nes", es="keep")
        check(rc == 1 and "prep step failed" in out and "did not put it back" in out and "attract-restore" in out and "RESULT: PASS" not in out,
              "prep refuses while a previous run left attract mode off (clear message naming attract-restore), nothing launched")
        check(es_timer() == "0", "the refused run left es_settings.xml alone")
        # the coordinator puts it back (here: the attract-restore step, the foreign game is not visible to pgrep any more)
        r = subprocess.run(["bash", os.path.join(HERE, "smoke_remote.sh"), "attract-restore"], env=target_env(), stdout=subprocess.PIPE, universal_newlines=True)
        check(r.returncode == 0 and es_timer() == ES_ORIG and "screensaver_restored=%s" % ES_ORIG in r.stdout, "attract-restore puts the original value back")
        # the next run must refuse: the kept backup's config differs from the file
        rc, out, _ = run("exec %s\n" % SLEEP, systems="nes", es="keep")
        check(rc == 1 and "prep step failed" in out and "previous run's backup" in out, "prep refuses an interrupted run's changed config (clear message), exit 1")
        check("RESULT: PASS" not in out, "prep refusal: nothing launched")
        open(ra_cfg, "w").write('video_driver = "gl"\n')       # put the config back by hand, as the message says
        check(sh(ra_cfg) == ra0, "(test setup) retroarch.cfg put back")
        rc, out, _ = run("exec %s\n" % SLEEP, systems="nes")
        check(rc == 0 and "RESULT: PASS nes" in out, "after the config is back, the next run starts normally")
    finally:
        foreign.kill()
        foreign.wait()

    # a leaked config (our own run): restored, and the backup is KEPT as evidence
    rc, out, _ = run('echo \'vsync = "false"\' >> "%s"\nexec %s\n' % (ra_cfg, SLEEP), systems="nes")
    check(rc == 1 and sh(ra_cfg) == ra0 and os.path.isdir(target_bak) and not os.path.exists(target_out),
          "a changed config: restored, HK_BAK kept, HK_OUT deleted")

    # the wrapper marker: a launcher that ignores COIN_DOJO_RETROARCH (system moved to RETROARCH_BY_SYSTEM) = FAIL
    rc, out, _ = run("exec %s\n" % SLEEP, systems="nes", launch=LAUNCH_SHIM)
    check(rc == 1 and "RESULT: FAIL nes RetroArch ran WITHOUT" in out, "WRAPPER MARKER: a RetroArch run that bypassed the wrapper = FAIL")
    check(not leftovers(), "bypassed wrapper: nothing left running")

    # the EXIT trap: SIGTERM to the step (ssh dropped) takes the emulator group with it
    envt = target_env(extra={"HK_WAIT": "30", "HK_GAP": "1"})
    subprocess.run(["bash", os.path.join(HERE, "smoke_remote.sh"), "prep"], env=envt, stdout=subprocess.PIPE)
    script(fly_stub, "#!/bin/sh\n" + ready_body("%s &\n%s &\nwait\n" % (SLEEP, SLEEP_B)))
    step = subprocess.Popen(["bash", os.path.join(HERE, "smoke_remote.sh"), "one", "dreamcast", "dc game.chd"], env=envt, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        check(until(lambda: sleepers(SLEEP, SLEEP_B) == [SLEEP, SLEEP_B]), "(test setup) the step started the forking flycast")
        step.terminate()
        step.wait(timeout=60)
        check(until(lambda: sleepers(SLEEP, SLEEP_B) == []), "EXIT TRAP: SIGTERM to the step kills the emulator's whole group")
    finally:
        step.kill()
        subprocess.run(["pkill", "-f", "(%s|%s)$" % (SLEEP, SLEEP_B)])
    check(not pkill_log(), "trap test: pkill never called")

    # --- 3c. attract mode (ES-DE screensaver) off for the run, the ORIGINAL value back after (#104) -------------
    import signal as _sig
    REMOTE = os.path.join(HERE, "smoke_remote.sh")
    write_es()
    es_before = hashlib.sha256(open(ES_XML, "rb").read()).hexdigest()

    # good run: timer 0 at the first launch (and at every launch), original value back after, byte-identical file, no copy anywhere
    rc, out, _ = run("exec %s\n" % SLEEP)
    check(rc == 0 and slog("launch-timer.log") == ["timer=0", "timer=0"], "attract: ScreensaverTimer is 0 at the first launch and at every launch")
    check(slog("restart.log") == ["timer=0", "timer=%s" % ES_ORIG], "attract: ES-DE restarted twice (kill -9 AppRun): once with 0 in the file, once with the original value")
    check(es_timer() == ES_ORIG and hashlib.sha256(open(ES_XML, "rb").read()).hexdigest() == es_before, "attract: the ORIGINAL value (%s, not a hardcoded one) is back, the rest of es_settings.xml is byte-identical" % ES_ORIG)
    check("attract mode off (ScreensaverTimer %s -> 0" % ES_ORIG in out and "attract mode restored (ScreensaverTimer back to %s" % ES_ORIG in out, "attract: the run says it turned attract mode off and put it back")
    check(slog("copies.log") == [] and copies_of_secret() == [], "NO COPY: no file besides es_settings.xml ever held the ScreenScraper password (scanned at each restart and at the end; scratch dirs, backups, results)")
    check(not os.path.exists(target_bak) and not os.path.exists(target_out), "attract: nothing left on the target after a clean run")
    check(not pkill_log(), "attract: only the AppRun restart used pkill, never by emulator name")

    # a value that is already 0: nothing to change, no restart, still 0
    rc, out, _ = run("exec %s\n" % SLEEP, systems="nes", es="0")
    check(rc == 0 and slog("restart.log") == [] and es_timer() == "0" and "attract mode: already_off" in out, "attract already 0: no restart, left alone")

    # BROKEN: a run that FAILs (dead emulator) still restores the original
    rc, out, _ = run("exit 1\n", systems="nes")
    check(rc == 1 and es_timer() == ES_ORIG and slog("restart.log") == ["timer=0", "timer=%s" % ES_ORIG], "attract: after a FAIL (dead emulator) the original value is restored and ES-DE restarted")
    # BROKEN: a leaking RetroArch (config changed) and a launch.py that bypasses the wrapper: still restored
    rc, out, _ = run('echo \'vsync = "false"\' >> "%s"\nexec %s\n' % (ra_cfg, SLEEP), systems="nes")
    check(rc == 1 and es_timer() == ES_ORIG and slog("restart.log")[-1] == "timer=%s" % ES_ORIG, "attract: restored after a config-changed FAIL too")
    open(ra_cfg, "w").write('video_driver = "gl"\n')
    shutil.rmtree(target_bak, ignore_errors=True)

    # BROKEN: no ScreensaverTimer line -> FAIL before any emulator starts, file untouched
    rc, out, _ = run("exec %s\n" % SLEEP, systems="nes", es=None)
    check(rc == 1 and "could not turn attract mode off" in out and "no ScreensaverTimer line" in out and not os.path.exists(argslog) and slog("restart.log") == [],
          "attract: no ScreensaverTimer line = FAIL, nothing launched, ES-DE not restarted")
    # BROKEN: getty@tty1 not active after the restart: loud, and the original is still tried back
    rc, out, _ = run("exec %s\n" % SLEEP, systems="nes", extra={"STUB_GETTY": "failed"})
    check(rc == 1 and "getty@tty1 is failed" in out and "NOT put back" in out and not os.path.exists(argslog), "attract: getty@tty1 failed after the restart = loud FAIL, nothing launched, restore reported as not done")
    check(os.path.exists(os.path.join(target_bak, "screensaver.orig")), "attract: the original value stays on the target until the restore worked")
    r = subprocess.run(["bash", REMOTE, "attract-restore"], env=target_env(), stdout=subprocess.PIPE, universal_newlines=True)
    check(r.returncode == 0 and es_timer() == ES_ORIG and not os.path.exists(os.path.join(target_bak, "screensaver.orig")), "attract: attract-restore (getty fine again) puts it back")
    shutil.rmtree(target_bak, ignore_errors=True)

    # the step itself refuses to touch the file while a game runs (a foreign game before the edit)
    write_es()
    for f in ("restart.log",):
        if os.path.exists(os.path.join(stubs, f)):
            os.unlink(os.path.join(stubs, f))
    envg = target_env(running="retroarch,AppRun")
    subprocess.run(["bash", REMOTE, "prep"], env=target_env(), stdout=subprocess.PIPE)
    r = subprocess.run(["bash", REMOTE, "attract-off"], env=envg, stdout=subprocess.PIPE, universal_newlines=True)
    check(r.returncode == 9 and "refused: a game is running" in r.stdout and hashlib.sha256(open(ES_XML, "rb").read()).hexdigest() == es_before and slog("restart.log") == [],
          "attract-off with a game running: exit 9, es_settings.xml untouched, ES-DE not restarted")
    check(not os.path.exists(os.path.join(target_bak, "screensaver.orig")), "attract-off refused: nothing recorded to restore")
    # ...and attract-restore with a game running does not restart ES-DE
    open(os.path.join(target_bak, "screensaver.orig"), "w").write(ES_ORIG + "\n")
    write_es("0")
    r = subprocess.run(["bash", REMOTE, "attract-restore"], env=envg, stdout=subprocess.PIPE, universal_newlines=True)
    check(r.returncode == 9 and es_timer() == "0" and slog("restart.log") == [] and "screensaver_restore_cmd=sed -i" in r.stdout,
          "attract-restore with a game running: no edit, no restart, prints the command")
    shutil.rmtree(target_bak, ignore_errors=True)

    # interrupts of the whole controller: Ctrl-C (SIGINT), SIGTERM, SIGHUP -> the original comes back
    def interrupted(signum):
        write_es()
        for f in ("restart.log", "launch-timer.log", "copies.log"):
            if os.path.exists(os.path.join(stubs, f)):
                os.unlink(os.path.join(stubs, f))
        shutil.rmtree(target_bak, ignore_errors=True)
        script(ra_stub, "#!/bin/sh\n" + TIMER_SH + ready_body("exec %s\n" % SLEEP))
        e = target_env()
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "smoke_launch.py"), "--target", "local", "--systems", "nes", "--table", small_tab,
                              "--expected", small_exp, "--out", os.path.join(tmp, "results-int"), "--wait", "30", "--gap", "1"],
                             env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, start_new_session=True,
                             preexec_fn=lambda: _sig.signal(_sig.SIGINT, _sig.SIG_DFL))   # a harness may start us with SIGINT ignored (background job)
        try:
            until(lambda: os.path.exists(READY_FILE) and slog("launch-timer.log"))
            started = slog("launch-timer.log") == ["timer=0"]
            os.killpg(p.pid, signum)     # what Ctrl-C does: the signal goes to the controller and its ssh/bash child alike
            try:
                o = p.communicate(timeout=60)[0]
            except subprocess.TimeoutExpired:
                p.kill()
                o = p.communicate()[0]
        finally:
            if p.poll() is None:
                p.kill()
        # the remote step's trap may still be finishing when the controller is gone: wait for its end state, bounded
        until(lambda: es_timer() == ES_ORIG and slog("restart.log")[-1:] == ["timer=%s" % ES_ORIG] and sleepers(SLEEP) == [] and not leftovers())
        return started, o

    for name, signum in (("Ctrl-C (SIGINT)", _sig.SIGINT), ("SIGTERM", _sig.SIGTERM), ("SIGHUP", _sig.SIGHUP)):
        started, o = interrupted(signum)
        check(started, "(test setup) %s: the emulator started with attract mode off" % name)
        check(es_timer() == ES_ORIG and slog("restart.log")[-1:] == ["timer=%s" % ES_ORIG], "INTERRUPT %s mid-run: the original ScreensaverTimer is back and ES-DE restarted" % name)
        check(len(slog("restart.log")) == 2, "INTERRUPT %s: ES-DE restarted exactly twice (the drop trap and the controller do not both restart it)" % name)
        if os.environ.get("DBG"): print(name, sleepers(SLEEP), leftovers(), subprocess.run("ps -axo pid,pgid,stat,command | grep -E '3133|smoke_' | grep -v grep", shell=True, stdout=subprocess.PIPE).stdout.decode())
        check(sleepers(SLEEP) == [] and not leftovers(), "INTERRUPT %s: no emulator left running" % name)
        check(copies_of_secret() == [], "INTERRUPT %s: no copy of es_settings.xml anywhere" % name)
        subprocess.run(["pkill", "-f", SLEEP + "$"])

    # ssh drop: only the remote step is hit (SIGHUP to the `one` step, no controller to clean up): its own trap restores
    write_es()
    for f in ("restart.log", "launch-timer.log"):
        if os.path.exists(os.path.join(stubs, f)):
            os.unlink(os.path.join(stubs, f))
    shutil.rmtree(target_bak, ignore_errors=True)
    envd = target_env(extra={"HK_WAIT": "30", "HK_GAP": "1"})
    subprocess.run(["bash", REMOTE, "prep"], env=envd, stdout=subprocess.PIPE)
    r = subprocess.run(["bash", REMOTE, "attract-off"], env=envd, stdout=subprocess.PIPE, universal_newlines=True)
    check(r.returncode == 0 and "attract=off" in r.stdout and es_timer() == "0", "(test setup) attract-off step: timer 0, ES-DE restarted")
    step = subprocess.Popen(["bash", REMOTE, "one", "nes", "game one.zip"], env=envd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        until(lambda: os.path.exists(READY_FILE) and slog("launch-timer.log"))
        step.send_signal(_sig.SIGHUP)
        step.wait(timeout=60)
        until(lambda: slog("restart.log") == ["timer=0", "timer=%s" % ES_ORIG] and sleepers(SLEEP) == [])
        check(es_timer() == ES_ORIG and slog("restart.log") == ["timer=0", "timer=%s" % ES_ORIG], "SSH DROP (SIGHUP to the remote step): the remote trap puts the original value back and restarts ES-DE")
        check(sleepers(SLEEP) == [], "SSH DROP: the emulator group died too")
    finally:
        step.kill()
        subprocess.run(["pkill", "-f", SLEEP + "$"])
    shutil.rmtree(target_bak, ignore_errors=True)
    check(copies_of_secret() == [], "NO COPY: still no file besides es_settings.xml holds the password at the end of the attract tests")

    # --- 3d. own pids only (#107, #114) and full-size shots (#114) -------------------------------------------
    # leftovers() counts only OUR marked sleeps: an unrelated sleep (another checkout / a parallel copy of this test) is invisible to it
    unrelated = subprocess.Popen(["sleep", "47999"], start_new_session=True)
    try:
        check(not leftovers(), "OWN PIDS: an unrelated parallel sleep (another checkout) is not counted as a leftover")
        rc, out, _ = run("exec %s\n" % SLEEP, systems="nes")
        check(rc == 0 and "RESULT: PASS nes" in out and not leftovers() and alive_pid(unrelated.pid),
              "OWN PIDS: a run passes with an unrelated sleep alive, which stays alive (nothing killed by name)")
        # BROKEN: a real own leftover (a child that escaped its process group) is still caught
        rc, out, _ = run("python3 -c \"import subprocess; subprocess.Popen(%r, start_new_session=True)\"\nexec %s\n" % (SLEEP.split(), SLEEP), systems="nes")
        check(leftovers(), "OWN PIDS (broken case): a real own leftover (marked sleep that escaped the group) IS detected")
        subprocess.run(["pkill", "-f", SLEEP + "$"])
        check(until(lambda: not leftovers()), "OWN PIDS: after cleaning up our own leftover the check is clean again")
    finally:
        unrelated.kill()
        unrelated.wait()

    def png_width(path):
        d = open(path, "rb").read(24)
        return int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big")

    def pulled_png(shot_args):
        out_root = os.path.join(tmp, "results-shot")
        shutil.rmtree(out_root, ignore_errors=True)
        envs = target_env()
        old = os.environ.copy()
        os.environ.clear()
        os.environ.update(envs)
        script(ra_stub, '#!/bin/sh\n' + TIMER_SH + ready_body('echo "$@" > "%s"\nexec %s\n' % (argslog, SLEEP)))
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sl.main(["--target", "local", "--systems", "nes", "--table", small_tab, "--expected", small_exp, "--out", out_root,
                              "--wait", T_WAIT, "--gap", T_GAP] + shot_args)
            if rc != 0:
                print("pulled_png: run failed:", buf.getvalue())
        finally:
            os.environ.clear()
            os.environ.update(old)
        d = os.path.join(out_root, os.listdir(out_root)[0])
        return rc, png_width(os.path.join(d, "nes.png"))

    write_es()
    rc, size = pulled_png([])
    check(rc == 0 and size == (1920, 1080), "SHOT default = full: the pulled screenshot is 1920x1080 (got %s)" % (size,))
    write_es()
    rc, size = pulled_png(["--shot", "full"])
    check(rc == 0 and size == (1920, 1080), "--shot full: 1920x1080")
    write_es()
    rc, size = pulled_png(["--shot", "small"])
    check(rc == 0 and size == (960, 540), "--shot small: half size 960x540 (got %s)" % (size,))
    check(not leftovers(), "shot tests: nothing left running")
    r = subprocess.run(["make", "-C", ROOT, "-n", "smoke-launch", "LIMIT=coin-dojo", "SHOT=small", "CABINET_SSH=true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    check('--shot "small"' in r.stdout, "make smoke-launch SHOT=small passes --shot small")
    r = subprocess.run(["make", "-C", ROOT, "-n", "smoke-launch", "CABINET_SSH=true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    check('--shot "full"' in r.stdout, "make smoke-launch default is SHOT=full")
    r = subprocess.run(["make", "-C", ROOT, "help"], stdout=subprocess.PIPE, universal_newlines=True)
    check("SHOT=full|small" in r.stdout, "make help lists SHOT=")

    # --- 4. the make target ---------------------------------------------------------------------------------
    r = subprocess.run(["make", "-C", ROOT, "smoke-launch", "LIMIT=bogus"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    check(r.returncode != 0 and "LIMIT must be" in r.stdout, "make smoke-launch LIMIT=bogus is refused")
    r = subprocess.run(["make", "-C", ROOT, "help"], stdout=subprocess.PIPE, universal_newlines=True)
    check("smoke-launch" in r.stdout and "SYSTEMS=a,b" in r.stdout, "make help lists smoke-launch and SYSTEMS=")
    # the make target through a stub ssh that runs the remote command here, with a game "running": refused, exit 9
    fake_ssh = os.path.join(tmp, "fake-ssh")
    script(fake_ssh, '#!/bin/bash\nexec bash -c "${@: -1}"\n')
    # HK_CONFIG: the deploy-class targets run config-check first (#118); the example file is a valid config.
    env = dict(os.environ, PATH=stubs + os.pathsep + os.environ["PATH"], STUB_DIR=stubs, STUB_RUNNING="retroarch,AppRun",
               HK_CONFIG=os.path.join(ROOT, "config", "local.example.yml"))
    r = subprocess.run(["make", "-C", ROOT, "smoke-launch", "CABINET_SSH=" + fake_ssh, "SMOKE_OUT=" + os.path.join(tmp, "make-out"), "SYSTEMS=nes"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=env)
    check(r.returncode != 0 and "cabinet-guard: a game is running" in r.stdout and "RESULT: PASS" not in r.stdout,
          "make smoke-launch against a cabinet with a game running is refused by cabinet-guard (before anything starts)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

sys.exit(finish())
