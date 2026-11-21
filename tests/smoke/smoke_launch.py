#!/usr/bin/env python3
"""Per-system launch smoke test (#48), Tier 0 of the regression suite. Stdlib only.

    make smoke-launch [SYSTEMS=nes,n64] [LIMIT=coin-dojo|coin-dojo-vm]
    smoke_launch.py --target cabinet|vm|local [--systems a,b] [--out DIR] [--wait 10] [--gap 3]

For each system in launch_expected.json it takes ONE ROM from smoke_games.tsv and starts it THROUGH
~/ES-DE/scripts/launch.py (what ES-DE runs for every game), in the live cage session, on top of ES-DE
(ES-DE's attract mode is off for the run: ScreensaverTimer -> 0, ES-DE restarted, and the ORIGINAL value put back
at the end, also on FAIL, Ctrl-C, HUP/TERM and ssh drop; never while a game that is not ours runs). PASS = the emulator is alive at --wait s and again --gap s later, the grim
screenshot is not near-uniform (all black / grey) and the two screenshots differ (not frozen), and no
config file changed. The target-side work is smoke_remote.sh (piped over ssh / limactl shell); this
file picks the games, judges the pictures and writes the contact sheet to <out>/<stamp>/.

One RESULT line per system, then `RESULT: PASS|FAIL smoke-launch ...`. A missing ROM / emulator / core
is FAIL on the cabinet and a loud SKIP in the VM (which holds no games); SKIP is never a PASS.
Exit: 0 all PASS/SKIP, 1 a FAIL, 2 bad usage or table, 8 ES-DE not up, 9 a game is already running.
"""
import argparse
import collections
import html
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "vm", "guest"))
import shotcheck  # noqa: E402  (read_ppm; the VM smoke test's own PPM reader)

# the pinned games: the maintainer's table in personal/ when present (#190), else the community example beside this file
PERSONAL_TABLE = os.path.join(ROOT, "personal", "tests", "smoke", "smoke_games.tsv")
TABLE = PERSONAL_TABLE if os.path.isfile(PERSONAL_TABLE) else os.path.join(HERE, "smoke_games.tsv")
EXPECTED = os.path.join(ROOT, "skills", "collections", "launch_expected.json")
REMOTE = os.path.join(HERE, "smoke_remote.sh")
VM_NAME = "coin-dojo-vm"
UNIFORM_AT = 0.995     # one colour (4 bits per channel) covers >= 99.5% of the pixels: black / grey / flat
MOVING_AT = 0.0005     # >= 0.05% of the pixels changed between the two shots
TILE_W, TILE_H, COLS = 320, 240, 5


# --- the pinned table ------------------------------------------------------------------------------
def load_table(path=TABLE):
    """[(system, rom, vm_rom, wait, motion)]; '-' = none."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            rows.append(tuple(line.rstrip("\n").split("\t")))
    return rows


def table_problems(rows, expected):
    bad, seen = [], set()
    for r in rows:
        if len(r) != 5 or not all(r):
            bad.append("row %r needs 5 non-empty tab separated columns (system, rom, vm_rom, wait, motion)" % (r,))
            continue
        s, rom, vm, wait, motion = r
        if not (wait.isdigit() and 1 <= int(wait) <= 120):
            bad.append("wait %r of %s must be a whole number of seconds, 1..120" % (wait, s))
        if motion not in ("yes", "no"):
            bad.append("motion %r of %s must be yes or no" % (motion, s))
        if s in seen:
            bad.append("system %s is listed twice" % s)
        seen.add(s)
        for what, v in (("rom", rom), ("vm_rom", vm)):
            if "/" in v or v.startswith(".") or "\n" in v:
                bad.append("%s %s of %s must be a plain file name" % (what, v, s))
    for s in sorted(set(expected) - seen):
        bad.append("system %s is in launch_expected.json but has no row" % s)
    for s in sorted(seen - set(expected)):
        bad.append("system %s has a row but is not in launch_expected.json" % s)
    return bad


# --- pictures ----------------------------------------------------------------------------------------
_QUANT = bytes(v & 0xF0 for v in range(256))


def dominant_fraction(pix):
    """Share of the pixels that have the most common colour (4 bits per channel)."""
    q = pix.translate(_QUANT)
    n = len(q) // 3
    if n == 0:
        return 1.0
    return collections.Counter(zip(q[0::3], q[1::3], q[2::3])).most_common(1)[0][1] / float(n)


def changed_fraction(pa, pb):
    """Share of the pixels whose colour moved by more than 16 in some channel."""
    if len(pa) != len(pb) or not pa:
        return 1.0
    n = changed = 0
    for i in range(0, len(pa) - 2, 3):
        n += 1
        if abs(pa[i] - pb[i]) > 16 or abs(pa[i + 1] - pb[i + 1]) > 16 or abs(pa[i + 2] - pb[i + 2]) > 16:
            changed += 1
    return changed / float(n)


def parse_ppm(data):
    """bytes of a binary PPM -> (w, h, rgb bytes)."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ppm") as tf:
        tf.write(data)
        tf.flush()
        return shotcheck.read_ppm(tf.name)


def fit(w, h, pix, tw=TILE_W, th=TILE_H):
    cols = [(x * w // tw) * 3 for x in range(tw)]
    rows = []
    for y in range(th):
        row = pix[(y * h // th) * w * 3:((y * h // th) + 1) * w * 3]
        rows.append(b"".join(row[c:c + 3] for c in cols))
    return b"".join(rows)


def write_png(path, w, h, rgb):
    def chunk(t, d):
        return len(d).to_bytes(4, "big") + t + d + (zlib.crc32(t + d) & 0xFFFFFFFF).to_bytes(4, "big")
    raw = b"".join(b"\0" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\0\0\0")
                 + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def contact_sheet(path, tiles):
    """tiles: [rgb bytes of TILE_W x TILE_H] in run order -> a COLS-wide PNG."""
    rows = (len(tiles) + COLS - 1) // COLS or 1
    blank = bytes([30, 30, 30]) * (TILE_W * TILE_H)
    tiles = list(tiles) + [blank] * (rows * COLS - len(tiles))
    lines = []
    for r in range(rows):
        for y in range(TILE_H):
            lines.append(b"".join(tiles[r * COLS + c][y * TILE_W * 3:(y + 1) * TILE_W * 3] for c in range(COLS)))
    write_png(path, COLS * TILE_W, rows * TILE_H, b"".join(lines))


# --- the target (cabinet over ssh, the VM through limactl, or this machine in tests) ------------------
class Target:
    def __init__(self, kind, ssh="", limactl="limactl"):
        self.kind, self.ssh, self.limactl = kind, ssh, limactl

    def _argv(self, remote):
        if self.kind == "cabinet":
            return shlex.split(self.ssh) + [remote]
        if self.kind == "vm":
            return [self.limactl, "shell", "--workdir", "/", VM_NAME, "bash", "-c", remote]
        return ["bash", "-c", remote]

    def run(self, argv, stdin=None, env=None, timeout=60):
        """Run argv on the target. -> (rc, stdout bytes). A timeout is rc 124."""
        remote = shlex.join(["env"] + ["%s=%s" % kv for kv in sorted((env or {}).items())] + list(argv))
        p = subprocess.Popen(self._argv(remote), stdin=subprocess.PIPE if stdin is not None else None, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            out, _ = p.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            return 124, b""
        except BaseException:
            # Ctrl-C / TERM / HUP of this controller: give the target step time to run its own trap (kill the emulator group,
            # put ES-DE's attract mode back) instead of SIGKILLing it; with ssh the dropped connection makes the remote side HUP.
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    p.kill()
            raise
        return p.returncode, out

    def step(self, args, env, timeout=60):
        """One smoke_remote.sh step. -> (rc, {key: value})."""
        with open(REMOTE, "rb") as fh:
            script = fh.read()
        rc, out = self.run(["bash", "-s", "--"] + list(args), stdin=script, env=env, timeout=timeout)
        kv = {}
        for line in out.decode("utf-8", "replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k] = v
        return rc, kv

    def pull(self, path):
        rc, data = self.run(["cat", path])
        return data if rc == 0 else b""


# --- the verdict -------------------------------------------------------------------------------------
def judge(info, shot_a, shot_b, wait=10, gap=3, motion=True):
    """info = smoke_remote.sh `one` output; shot_a / shot_b = (w, h, rgb) or None. -> (PASS|FAIL|SKIP, detail)."""
    res = info.get("result", "FAIL")
    if res in ("SKIP", "FAIL"):
        return res, info.get("reason", "no reason given")
    if info.get("state") != "alive":
        return "FAIL", "emulator died at %s s (rc %s): %s" % (info.get("died_at", "?"), info.get("rc", "?"), info.get("log", "").strip() or "no log")
    if info.get("cfg_changed"):
        return "FAIL", "config file changed by the test (restored): %s" % info["cfg_changed"]
    if info.get("leftover"):
        return "FAIL", "process left over after SIGKILL (pid %s, killed)" % info["leftover"]
    if not shot_a:
        return "FAIL", "alive at %d s but no screenshot (grim failed?)" % wait
    dom = dominant_fraction(shot_a[2])
    if dom >= UNIFORM_AT:
        return "FAIL", "screenshot is near-uniform (%.1f%% one colour): black or grey screen" % (dom * 100)
    if motion:
        if not shot_b or shot_b[:2] != shot_a[:2]:
            return "FAIL", "no second screenshot to check the picture moves"
        ch = changed_fraction(shot_a[2], shot_b[2])
        if ch < MOVING_AT:
            return "FAIL", "frozen: the two shots %d s apart are the same (%.3f%% changed)" % (gap, ch * 100)
        return "PASS", "alive at %d s and %d s, picture moves (%.1f%% changed), %.0f%% one colour" % (wait, wait + gap, ch * 100, dom * 100)
    return "PASS", "alive at %d s, %.0f%% one colour (motion check off)" % (wait, dom * 100)


def pull_shot(target, info, key):
    if not info.get(key):
        return None
    try:
        return parse_ppm(target.pull(info[key]))
    except (ValueError, IndexError, OSError):
        return None


# --- the run -----------------------------------------------------------------------------------------
def say(msg):
    print(msg, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", choices=("cabinet", "vm", "local"), default="cabinet")
    ap.add_argument("--ssh", default="", help="the ssh command of the cabinet (make passes $(CABINET_SSH))")
    ap.add_argument("--limactl", default="limactl")
    ap.add_argument("--systems", default="", help="comma separated; default every system in the table")
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/coin-dojo/ws/smoke"))
    ap.add_argument("--table", default=TABLE)
    ap.add_argument("--expected", default=EXPECTED)
    ap.add_argument("--wait", type=int, default=None, help="override every row's wait column (seconds)")
    ap.add_argument("--gap", type=int, default=3)
    ap.add_argument("--shot", choices=("full", "small"), default="full", help="kept screenshots: full = the cabinet's native size (default), small = half")
    ap.add_argument("--no-motion", action="store_true", help="do not require the two shots to differ (always off in the VM: its test ROM is one still picture)")
    ap.add_argument("--esde-wait", type=int, default=30, help="vm: seconds to wait for ES-DE after starting the session")
    a = ap.parse_args(argv)
    t0 = time.time()

    with open(a.expected, encoding="utf-8") as fh:
        expected = json.load(fh)["systems"]
    rows = load_table(a.table)
    bad = table_problems(rows, expected)
    if bad:
        for b in bad:
            say("RESULT: FAIL smoke-launch: table: %s" % b)
        return 2
    want = [s for s in a.systems.split(",") if s]
    unknown = [s for s in want if s not in expected]
    if unknown:
        say("RESULT: FAIL smoke-launch: unknown system(s) %s (known: %s)" % (",".join(unknown), ",".join(sorted(expected))))
        return 2
    rows = [r for r in rows if not want or r[0] in want]

    vm = a.target == "vm"
    target = Target(a.target, a.ssh, a.limactl)
    env = {"HK_GAP": str(a.gap), "HK_SHOT": a.shot, "HK_MISSING": "SKIP" if vm else "FAIL"}
    # XDG_RUNTIME_DIR (/run/user/<uid of the login user>) and WAYLAND_DISPLAY come from smoke_remote.sh itself.
    if a.target == "cabinet" and not a.ssh:
        say("RESULT: FAIL smoke-launch: --ssh is required for the cabinet")
        return 2
    stamp = time.strftime("%Y%m%d-%H%M%S") + ("-vm" if vm else "")
    outdir = os.path.join(a.out, stamp)
    os.makedirs(outdir, exist_ok=True)

    started_session = False
    results, tiles, links = [], [], []
    rc_final = 0
    prepped = False
    before = {}
    try:
        if vm:
            rc, out = target.run(["sh", "-c", 'pgrep -u "$(id -u)" -x cage'])
            if rc != 0:
                rc, _ = target.run(["hk-session", "start"], timeout=60)
                if rc != 0:
                    say("RESULT: FAIL smoke-launch: could not start the VM session (hk-session start)")
                    return 1
                started_session = True
            target.run(["sh", "-c", "[ -f /data/roms/nes/coin-dojo-smoke.nes ] || python3 -I /usr/local/lib/coin-dojo-vm/mknesrom.py /data/roms/nes/coin-dojo-smoke.nes"])
        # Guards: never start while a game runs (the maintainer may be playing); ES-DE must be up (wait for it in the VM).
        deadline = time.time() + (a.esde_wait if started_session else 0)
        while True:
            rc, kv = target.step(["guard"], env)
            if rc != 8 or time.time() >= deadline:
                break
            time.sleep(2)
        if rc == 9:
            say("RESULT: FAIL smoke-launch: refused, a game is already running (pid %s); quit it first (Select+Start). Nothing was started." % kv.get("busy", "?"))
            return 9
        if rc == 8:
            say("RESULT: FAIL smoke-launch: refused, ES-DE (AppRun / es-de) is not running; nothing to launch on top of")
            return 8
        if rc != 0:
            say("RESULT: FAIL smoke-launch: guard step failed (rc %d): cannot reach the %s?" % (rc, a.target))
            return 1
        rc, kv = target.step(["prep"], env)
        if rc != 0:
            say("RESULT: FAIL smoke-launch: prep step failed (rc %d)%s" % (rc, ": " + kv["reason"] if kv.get("reason") else ""))
            return 1
        prepped = True
        if vm:
            say("smoke-launch: attract mode left alone in the VM (no es_settings.xml handling there)")
        else:
            rc, akv = target.step(["attract-off"], env, timeout=180)
            if rc == 9:
                say("RESULT: FAIL smoke-launch: refused, a game is running (pid %s); attract mode and ES-DE untouched" % akv.get("busy", "?"))
                rc_final, rows = 9, []
            elif rc != 0:
                say("RESULT: FAIL smoke-launch: could not turn attract mode off (rc %d): %s%s" % (
                    rc, akv.get("reason", "no output"), "; getty@tty1 is %s" % akv["getty"] if akv.get("getty") else ""))
                rc_final, rows = 1, []
            elif akv.get("attract") == "off":
                say("smoke-launch: attract mode off (ScreensaverTimer %s -> 0, ES-DE restarted, log %s); the original value is put back at the end" % (akv.get("screensaver_orig", "?"), akv.get("esde_log", "?")))
            else:
                say("smoke-launch: attract mode: %s" % akv.get("attract", "unchanged"))
        before = {k[len("sha_before."):]: v for k, v in kv.items() if k.startswith("sha_before.")}
        say("smoke-launch: %s, %d system(s), wait per smoke_games.tsv%s + %d s, results in %s" % (a.target, len(rows), "" if a.wait is None else " (forced %d s)" % a.wait, a.gap, outdir))
        for system, rom, vm_rom, row_wait, row_motion in rows:
            game = vm_rom if vm else rom
            t1 = time.time()
            wait = int(row_wait) if a.wait is None else a.wait
            env["HK_WAIT"] = str(wait)
            say("smoke-launch: [%d/%d] %s: %s ..." % (len(results) + 1, len(rows), system, "skipped, no game" if game == "-" else "launching %s, check at %d s" % (game, wait)))
            if game == "-":
                info = {"result": "SKIP", "reason": "no game pinned for %s in smoke_games.tsv (%s)" % (system, "VM holds no games" if vm else "no ROMs on the cabinet")}
            else:
                rc, info = target.step(["one", system, game], env, timeout=wait + a.gap + 60)
                if rc == 9:
                    say("RESULT: FAIL %s: refused, a game started during the run (pid %s); it was left alone, nothing killed; stopping" % (system, info.get("foreign") or info.get("busy", "?")))
                    rc_final = 9
                    break
                if rc not in (0,) and "result" not in info:
                    info = {"result": "FAIL", "reason": "target step failed (rc %d): %s" % (rc, "timeout" if rc == 124 else "no output")}
            shot_a = pull_shot(target, info, "shot_a")
            shot_b = pull_shot(target, info, "shot_b")
            verdict, detail = judge(info, shot_a, shot_b, wait, a.gap, motion=row_motion == "yes" and not (a.no_motion or vm))   # the VM's only game is a static test ROM
            png = target.pull(info["png"]) if info.get("png") else b""
            if png:
                with open(os.path.join(outdir, system + ".png"), "wb") as fh:
                    fh.write(png)
            tiles.append(fit(*shot_a) if shot_a else bytes({"FAIL": (170, 0, 0), "SKIP": (0, 0, 90)}.get(verdict, (30, 30, 30))) * (TILE_W * TILE_H))
            line = "RESULT: %s %s %s (%s, %.0f s)" % (verdict, system, detail, game, time.time() - t1)
            say(line)
            results.append((system, verdict, detail, game, bool(png)))
            links.append(line)
    finally:
        fin = {}
        if prepped:
            rc, fin = target.step(["finish"], env, timeout=180)
            if fin.get("screensaver_not_restored"):
                rc_final = rc_final or 1
                say("RESULT: FAIL smoke-launch: ES-DE attract mode was NOT put back (ScreensaverTimer was %s)%s" % (
                    fin.get("screensaver_orig", "?"), "; getty@tty1 is %s" % fin["getty"] if fin.get("getty") else ""))
                say("smoke-launch: to restore it by hand, with no game running: %s" % fin.get("screensaver_restore_cmd", "bash smoke_remote.sh attract-restore (HK_BAK dir kept on the target)"))
                if fin.get("reason"):
                    say("smoke-launch: %s" % fin["reason"])
            elif fin.get("screensaver_restored"):
                say("smoke-launch: attract mode restored (ScreensaverTimer back to %s, ES-DE restarted)" % fin["screensaver_restored"])
            if fin.get("getty") not in (None, "active"):
                say("RESULT: FAIL smoke-launch: getty@tty1 is %s after the ES-DE restart; the cabinet session may be down (runbooks/cabinet-recovery.md 3c)" % fin["getty"])
                rc_final = rc_final or 1
            if rc in (7, 9) or fin.get("cfg_changed") or fin.get("leftover"):
                rc_final = rc_final or (9 if rc == 9 else 1)
                if rc == 9:
                    say("RESULT: FAIL smoke-launch: a game started during the run (pid %s); left alone, nothing killed, configs not restored" % fin.get("foreign", "?"))
                if fin.get("cfg_changed"):
                    say("RESULT: FAIL smoke-launch: config changed during the run%s: %s" % (" (NOT restored: a game was running)" if fin.get("cfg_not_restored") else " and restored", fin["cfg_changed"]))
                if fin.get("leftover"):
                    say("RESULT: FAIL smoke-launch: LEFTOVER emulator process after the run (pid %s), killed" % fin["leftover"])
            elif rc != 0 and not fin.get("screensaver_not_restored"):
                say("RESULT: FAIL smoke-launch: finish step failed (rc %d)" % rc)
                rc_final = rc_final or 1
            for k in sorted(fin):
                if k.startswith("sha_after."):
                    n = k[len("sha_after."):]
                    same = "unchanged" if before.get(n) == fin[k] else "CHANGED"
                    say("smoke-launch: %s sha256 %s -> %s (%s)" % (n, before.get(n, "?")[:12], fin[k][:12], same))
        if started_session:
            target.run(["hk-session", "stop"], timeout=60)

    if results:
        contact_sheet(os.path.join(outdir, "sheet.png"), tiles)
        with open(os.path.join(outdir, "results.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(links) + "\n")
        cells = "".join("<figure><figcaption><b>%s</b> %s<br>%s</figcaption>%s</figure>" % (
            html.escape(s), v, html.escape(d), '<img src="%s.png" width="480">' % s if p else "") for s, v, d, _g, p in results)
        with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<!doctype html><meta charset=utf-8><title>smoke-launch %s</title><body style='font:13px sans-serif;background:#111;color:#ddd'>"
                     "<img src=sheet.png><div style='display:flex;flex-wrap:wrap;gap:12px'>%s</div>" % (stamp, cells))
        say("smoke-launch: contact sheet %s" % os.path.join(outdir, "sheet.png"))
    n = collections.Counter(v for _s, v, _d, _g, _p in results)
    if n["FAIL"]:
        rc_final = rc_final or 1
    secs = time.time() - t0
    say("RESULT: %s smoke-launch %s: %d PASS, %d FAIL, %d SKIP in %.0f s" % ("FAIL" if rc_final else "PASS", a.target, n["PASS"], n["FAIL"], n["SKIP"], secs))
    return rc_final


def _terminate(_sig, _frm):
    raise SystemExit(130)      # unwinds through main's finally: the original ScreensaverTimer goes back


if __name__ == "__main__":
    for _s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(_s, _terminate)
    sys.exit(main())
