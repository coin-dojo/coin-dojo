#!/usr/bin/env python3
"""Fast checks for the RetroArch 1.22 overlay preview (skills/local-preview/retroarch-122/), no docker.
pytest-compatible, or run directly:  python3 skills/local-preview/tests/test_ra122_preview.py  (~6 s)
The run script runs here on the host against fakes (cage, nc, grim, wlr-randr, compare, RetroArch); the wrapper
against a fake docker. Broken cases (#175) that MUST fail loudly with a named error and exit 3, never a pixel diff:
cage exits 1, cage makes no socket, RetroArch segfaults (139), RetroArch never answers, grim fails, the run
writes no verdict (a stale one from an earlier run must not count), diff.txt holds no numbers. Also: wrong argument
count, a BUILDINFO not v1.22.2 or without the EGL patch MUST exit 2; GET_STATUS is never sent (it segfaults 1.22.2).
Opt-in real run: RA122_PREVIEW_SMOKE=1 RA122_SMOKE_RA=<patched ra122 dir> RA122_SMOKE_DIR=<dir with core/, rom/<one
file>, overlays/, override.cfg> (docker, ~1 min) MUST PASS."""
import glob
import os
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
D = os.path.join(ROOT, "skills", "local-preview", "retroarch-122")
WRAP = os.path.join(D, "overlay-preview-122.sh")
RUN = os.path.join(D, "run-ra122.sh")
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402  (shared helper, #137)

PATCHED = "tag: v1.22.2\npatches: 0001-egl-loop-only-matched-configs.patch 0002-wayland-gles2-request-on-gles3-build.patch\n"

FAKES = {
    # cage: fail = exits 1 like "failed to create display"; nosock = never makes a socket; ok = socket + run the child
    "cage": """#!/bin/bash
case "${FAKE_CAGE:-ok}" in
  fail) echo "failed to create display" >&2; exit 1 ;;
  nosock) exec sleep 30 ;;
esac
touch "$XDG_RUNTIME_DIR/wayland-0"
while [ "$1" != -- ]; do shift; done; shift
"$@"; rc=$?; rm -f "$XDG_RUNTIME_DIR/wayland-0"; exit $rc
""",
    # nc: logs every command; answers VERSION only once the fake RetroArch is up (like a real one), unless
    # FAKE_NC=silent. Answering for a crashed RetroArch raced the segv case under load (#192).
    "nc": """#!/bin/bash
in=$(cat); echo "$in" >> "$FAKE_LOG"
[ "${FAKE_NC:-ok}" = ok ] && [ "$in" = VERSION ] && [ -e "$OUT/fake-ra-up" ] && echo 1.22.2
exit 0
""",
    "grim": """#!/bin/bash
[ "${FAKE_GRIM:-ok}" = fail ] && { echo "grim: failed to create display" >&2; exit 1; }
touch "$1"
""",
    "wlr-randr": "#!/bin/bash\necho 'HEADLESS-1 \"Headless output 1\"'\n",
    "compare": "#!/bin/bash\necho 0 >&2\n",
    # docker for the wrapper: image inspect ok; run writes the requested verdict/diff into FAKE_OUT and exits FAKE_RC
    "docker": """#!/bin/bash
[ "$1" = run ] || exit 0
[ -n "${FAKE_STARTUP:-}" ] && echo "$FAKE_STARTUP" > "$FAKE_OUT/startup.txt"
[ -n "${FAKE_DIFF:-}" ] && echo "$FAKE_DIFF" > "$FAKE_OUT/diff.txt"
exit "${FAKE_RC:-0}"
""",
}
FAKE_RA = """#!/bin/bash
[ "${FAKE_RA:-ok}" = segv ] && exit 139
touch "$OUT/fake-ra-up"
exec sleep 30
"""


def sh(args, env=None, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


def write_exec(path, body):
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def sandbox(t):
    """Fake tools + paths under t; returns the env for run-ra122.sh."""
    b = os.path.join(t, "bin")
    os.makedirs(b)
    for name, body in FAKES.items():
        write_exec(os.path.join(b, name), body)
    write_exec(os.path.join(t, "retroarch"), FAKE_RA)
    for d in ("out", "work", "rom", "core"):
        os.makedirs(os.path.join(t, d))
    open(os.path.join(t, "override.cfg"), "w").write('input_overlay_enable = "false"\n')
    open(os.path.join(t, "rom", "game.md"), "w").close()
    env = dict(os.environ)
    env.update(PATH=b + os.pathsep + env["PATH"], OUT=os.path.join(t, "out"), WORK=os.path.join(t, "work"),
               RUN_DIR=os.path.join(t, "xdg"), OVERRIDE=os.path.join(t, "override.cfg"),
               RA_BIN=os.path.join(t, "retroarch"), CORE=os.path.join(t, "core", "x_libretro.so"),
               ROM_DIR=os.path.join(t, "rom"), ROM="game.md", READY_WAIT="10", BOOT="0", SETTLE="0", HOLD="0",
               FAKE_LOG=os.path.join(t, "nc.log"))
    return env


def run_case(**fake):
    """Run run-ra122.sh against the fakes; returns (result, startup.txt, files in out, commands sent)."""
    with tempfile.TemporaryDirectory() as t:
        env = sandbox(t)
        env.update(fake)
        r = sh(["bash", RUN], env=env)
        out = env["OUT"]
        startup = open(os.path.join(out, "startup.txt")).read().strip() if os.path.exists(
            os.path.join(out, "startup.txt")) else ""
        sent = open(env["FAKE_LOG"]).read().split() if os.path.exists(env["FAKE_LOG"]) else []
        return r, startup, sorted(os.listdir(out)), sent


def test_files():
    for p in (WRAP, RUN):
        check(os.access(p, os.X_OK), "%s is executable" % os.path.basename(p))
        check(sh(["bash", "-n", p]).returncode == 0, "%s parses" % os.path.basename(p))
    check(os.path.isfile(os.path.join(D, "Dockerfile")), "Dockerfile exists")


def test_wrapper_args():
    r = sh([WRAP, "a", "b"])
    check(r.returncode == 2 and "usage" in r.stderr, "wrapper: bad argument count MUST exit 2")
    r = sh([WRAP])
    check(r.returncode == 2, "wrapper: no arguments MUST exit 2")


def test_wrapper_buildinfo():
    with tempfile.TemporaryDirectory() as t:
        open(os.path.join(t, "BUILDINFO"), "w").write("tag: v1.20.0\n")
        r = sh([WRAP, t, t, "rom.md", t, "o.cfg", t])
        check(r.returncode == 2 and "v1.22.2" in r.stderr, "wrapper: BUILDINFO not v1.22.2 MUST exit 2")
        r = sh([WRAP, "/nonexistent", t, "rom.md", t, "o.cfg", t])
        check(r.returncode == 2, "wrapper: missing BUILDINFO MUST exit 2")
        # the cabinet's retroarch.unpatched: right tag, no patches line -> segfaults at EGL (#175)
        open(os.path.join(t, "BUILDINFO"), "w").write("tag: v1.22.2\ncommit: 69a4f0e\n")
        r = sh([WRAP, t, t, "rom.md", t, "o.cfg", t])
        check(r.returncode == 2 and "0001-egl-loop-only-matched-configs" in r.stderr,
              "wrapper: unpatched 1.22.2 BUILDINFO MUST exit 2 and name the EGL patch")


def wrap_case(stale=None, **fake):
    with tempfile.TemporaryDirectory() as t:
        env = sandbox(t)
        ra = os.path.join(t, "ra")
        os.makedirs(ra)
        open(os.path.join(ra, "BUILDINFO"), "w").write(PATCHED)
        out = env["OUT"]
        for name, body in (stale or {}).items():
            open(os.path.join(out, name), "w").write(body)
        env.update(FAKE_OUT=out, **fake)
        return sh([WRAP, ra, t, os.path.join(t, "rom", "game.md"), t, env["OVERRIDE"], out], env=env)


def last_result(r):
    lines = [x for x in r.stdout.splitlines() if x.startswith("RESULT:")]
    return lines[-1] if lines else ""


def test_wrapper_verdicts():
    r = wrap_case(FAKE_STARTUP="alive 1.22.2", FAKE_DIFF="1.52466e+06 0 12382")
    check(r.returncode == 0 and last_result(r) == "RESULT: PASS", "wrapper: alive + good diff PASSes")
    r = wrap_case(FAKE_STARTUP="alive 1.22.2", FAKE_DIFF="1.5e+06 37 12382")
    check(r.returncode == 1 and last_result(r) == "RESULT: FAIL", "wrapper: game moving while open MUST FAIL (1)")
    # broken cases: the session never started -> named error, exit 3, no pixel numbers
    r = wrap_case(FAKE_STARTUP="CAGE_START_FAILED: cage exited with 1", FAKE_RC="3")
    check(r.returncode == 3 and "CAGE_START_FAILED" in r.stderr and last_result(r) == "RESULT: FAIL"
          and "pixels changed" not in r.stdout, "wrapper: cage failure MUST exit 3 named, no pixel diff")
    r = wrap_case(FAKE_RC="125")
    check(r.returncode == 3 and "NO_VERDICT" in r.stderr, "wrapper: run without a verdict MUST exit 3 (NO_VERDICT)")
    r = wrap_case(stale={"startup.txt": "alive 1.22.2\n", "diff.txt": "1.5e+06 0 100\n"}, FAKE_RC="3")
    check(r.returncode == 3 and "NO_VERDICT" in r.stderr, "wrapper: a stale verdict from an earlier run MUST NOT count")
    r = wrap_case(FAKE_STARTUP="alive 1.22.2", FAKE_DIFF="compare: compare: compare:")
    check(r.returncode == 3 and "DIFF_FAILED" in r.stderr and "pixels changed" not in r.stdout,
          "wrapper: diff.txt without numbers MUST exit 3 (DIFF_FAILED)")


def test_run_good_path():
    r, startup, files, sent = run_case()
    check(r.returncode == 0 and startup == "alive 1.22.2", "run: fakes start -> alive 1.22.2, exit 0 (%s)" % r.stderr.strip())
    check("diff.txt" in files and "4-closed-b.png" in files, "run: all five shots + diff.txt written")
    check(sent.count("OVERLAY_NEXT") == 2 and sent.count("PAUSE_TOGGLE") == 2, "run: overlay opened and closed")
    check("GET_STATUS" not in sent, "run: GET_STATUS is never sent (segfaults RA 1.22.2, #175)")


def test_run_broken_cases():
    cases = [
        ({"FAKE_CAGE": "fail"}, "CAGE_START_FAILED", "cage exits 1"),
        ({"FAKE_CAGE": "nosock", "READY_WAIT": "2"}, "CAGE_START_FAILED", "cage makes no Wayland socket"),
        ({"FAKE_RA": "segv"}, "RA_EXITED", "RetroArch segfaults (139)"),
        ({"FAKE_NC": "silent", "READY_WAIT": "2"}, "RA_NO_ANSWER", "RetroArch never answers VERSION"),
        ({"FAKE_GRIM": "fail"}, "SHOT_FAILED", "grim fails"),
    ]
    for fake, code, what in cases:
        r, startup, files, _ = run_case(**fake)
        check(r.returncode == 3 and startup.startswith(code + ":") and ("ERROR: " + code) in r.stderr
              and "diff.txt" not in files, "run: %s MUST exit 3 with %s and no diff (%s)" % (what, code, startup))
    r, startup, _, _ = run_case(FAKE_RA="segv")
    check("139 (segfault)" in startup, "run: the segfault exit code is named")


def test_run_script_content():
    s = open(RUN).read()
    for needle in ("network_cmd_enable", "55355", "OVERLAY_NEXT", "PAUSE_TOGGLE", "grim", "wlr-randr",
                   "1920x1080", "custom_viewport_width = \"1440\"", "custom_viewport_x = \"240\"",
                   "aspect_ratio_index = \"22\"", "input_overlay_enable = \"true\""):
        check(needle in s, "run script has %s" % needle)
    check("$WORK/override.cfg" in s and "sed -i" not in s, "override copied, never edited in place")
    check("pkill" not in s, "run script stops its own pids, never pkill")
    w = open(WRAP).read()
    check("100000" in w and "RESULT: PASS" in w and "RESULT: FAIL" in w, "wrapper keeps the PASS rule")


def test_real_smoke():
    if os.environ.get("RA122_PREVIEW_SMOKE") != "1":
        skip("real RA 1.22 overlay run (set RA122_PREVIEW_SMOKE=1, RA122_SMOKE_RA, RA122_SMOKE_DIR)")
        return
    ra, a = os.environ["RA122_SMOKE_RA"], os.environ["RA122_SMOKE_DIR"]
    rom = sorted(glob.glob(os.path.join(a, "rom", "*")))[0]
    out = os.path.join(a, "out-smoke-175")
    r = sh([WRAP, ra, os.path.join(a, "core"), rom, os.path.join(a, "overlays"), os.path.join(a, "override.cfg"), out],
           timeout=600)
    print(r.stdout + r.stderr)
    check(r.returncode == 0 and last_result(r) == "RESULT: PASS", "real RA 1.22.2 overlay preview PASSes")


if __name__ == "__main__":
    main(globals())
