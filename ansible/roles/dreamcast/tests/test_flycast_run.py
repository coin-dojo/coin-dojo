#!/usr/bin/env python3
"""#179: flycast-run.sh passes a per-game render height (hk_flycast_resolution_by_game) as a transient
`-config config:rend.Resolution=<h>`, only for the named ROM, matched without case; every other game gets no extra
argument. The template is rendered here with a small stand-in for the role's Jinja (the loop, `quote`, `int`) and run
under bash with a fake flycast that records its arguments. Broken cases: another game, a name that only contains
the key, a ROM with spaces; the role's assert refuses shell characters and heights outside 240-2160.
#187: the launcher exports dreamcast_gui_env (FLYCAST_GUI_43, FLYCAST_FPS_TOPRIGHT) before flycast starts.

    python3 ansible/roles/dreamcast/tests/test_flycast_run.py
"""
import os
import re
import shlex
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main  # noqa: E402

TPL = open(os.path.join(ROLE, "templates", "flycast-run.sh.j2")).read()
TASKS = open(os.path.join(ROLE, "tasks", "main.yml")).read()
DECL = open(os.path.join(REPO, "config", "variables.d", "emulators.yml")).read()


GUI_ENV = {"FLYCAST_FPS_TOPRIGHT": "1", "FLYCAST_GUI_43": "1"}


def render(by_game, ddir, uid=1000, gui_env=GUI_ENV):
    """Stand-in for Jinja on this one template: the per-game loop, then the plain variables."""
    m = re.search(r"\{% for name, h in dreamcast_resolution_by_game \| dictsort %\}\n(.*?)\{% endfor %\}\n", TPL, re.S)
    assert m, "per-game loop not found in flycast-run.sh.j2"
    line = m.group(1)
    body = "".join(line.replace("{{ name | quote }}", shlex.quote(k)).replace("{{ h | int }}", str(int(v)))
                   for k, v in sorted(by_game.items()))
    out = TPL[:m.start()] + body + TPL[m.end():]
    e = re.search(r"\{% for k, v in dreamcast_gui_env \| dictsort %\}\n(.*?)\{% endfor %\}\n", out, re.S)
    assert e, "gui env loop not found in flycast-run.sh.j2"
    ebody = "".join(e.group(1).replace("{{ k }}", k).replace("{{ v | quote }}", shlex.quote(v))
                    for k, v in sorted(gui_env.items()))
    out = out[:e.start()] + ebody + out[e.end():]
    out = out.replace("{{ dreamcast_dir }}", ddir).replace("{{ dreamcast_uid }}", str(uid))
    assert "{{" not in out and "{%" not in out, "unrendered Jinja left"
    return out


def run(by_game, rom, gui_env=GUI_ENV, want_env=False):
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "flycast"), "w") as fh:          # fake flycast: one argument per line
            fh.write('#!/bin/bash\nfor a in "$@"; do printf "%%s\\n" "$a"; done > %s/args\n'
                     'env | grep "^FLYCAST_" | sort > %s/env\n' % (d, d))
        with open(os.path.join(d, "flycast-exit-combo.py"), "w") as fh:
            fh.write("import sys\n")
        os.chmod(os.path.join(d, "flycast"), 0o755)
        script = os.path.join(d, "run.sh")
        with open(script, "w") as fh:
            fh.write(render(by_game, d, gui_env=gui_env))
        r = subprocess.run(["bash", script, rom], capture_output=True, text=True, timeout=20,
                           env=dict(os.environ, XDG_RUNTIME_DIR=d, WAYLAND_DISPLAY="x"))
        args = open(os.path.join(d, "args")).read().splitlines() if os.path.exists(os.path.join(d, "args")) else None
        if want_env:
            return r.returncode, open(os.path.join(d, "env")).read().splitlines()
        return r.returncode, args


def test_default_is_ikaruga_native():
    check(re.search(r"(?ms)^  hk_flycast_resolution_by_game:.*?^    default: \{Ikaruga: 480\}\s*$", DECL) is not None,
          "default: Ikaruga at 480 (the maintainer 2026-11-16, sluggish at 2x)")


def test_named_game_gets_the_transient_height():
    rc, args = run({"Ikaruga": 480}, "/data/roms/dreamcast/Ikaruga.chd")
    check(rc == 0 and args == ["-config", "config:rend.Resolution=480", "/data/roms/dreamcast/Ikaruga.chd"],
          "Ikaruga.chd starts with -config config:rend.Resolution=480 (%r)" % (args,))
    rc, args = run({"Ikaruga": 480}, "/data/roms/dreamcast/IKARUGA.gdi")
    check(args and args[:2] == ["-config", "config:rend.Resolution=480"], "matched without case (IKARUGA.gdi)")
    rc, args = run({"Ikaruga (Japan)": 480, "Rez": 720}, "/data/roms/dreamcast/Ikaruga (Japan).chd")
    check(args == ["-config", "config:rend.Resolution=480", "/data/roms/dreamcast/Ikaruga (Japan).chd"],
          "a ROM name with spaces and brackets matches as a whole")


def test_gui_env_reaches_flycast():
    rc, env = run({}, "/data/roms/dreamcast/Ikaruga.chd", want_env=True)
    check(rc == 0 and env == ["FLYCAST_FPS_TOPRIGHT=1", "FLYCAST_GUI_43=1"],
          "#187 flycast starts with FLYCAST_GUI_43=1 and FLYCAST_FPS_TOPRIGHT=1 (%r)" % (env,))
    rc, env = run({}, "/data/roms/dreamcast/Ikaruga.chd", gui_env={}, want_env=True)
    check(env == [], "BROKEN: an empty dreamcast_gui_env exports nothing (%r)" % (env,))


def test_broken_other_games_get_nothing_extra():
    for rom in ("/data/roms/dreamcast/Crazy Taxi.chd", "/data/roms/dreamcast/Ikaruga Prototype.chd",
                "/data/roms/dreamcast/NotIkaruga.chd"):
        rc, args = run({"Ikaruga": 480}, rom)
        check(rc == 0 and args == [rom], "BROKEN: %s starts with no extra argument (%r)" % (os.path.basename(rom), args))
    rc, args = run({}, "/data/roms/dreamcast/Ikaruga.chd")
    check(args == ["/data/roms/dreamcast/Ikaruga.chd"], "an empty map adds nothing")


def test_broken_role_refuses_unsafe_entries():
    m = re.search(r"item\.key is match\('(.*?)'\)", TASKS)
    check(m is not None and "item.value | int >= 240 and item.value | int <= 2160" in TASKS
          and "item.value | string is match('^[0-9]+$')" in TASKS, "the role asserts names and heights before templating")
    rx = re.compile(m.group(1)) if m else None
    for bad in ("Ika$(reboot)", "a;b", "x`y`", "a'b", "a\nb", ""):
        check(rx is not None and not rx.match(bad), "BROKEN: name %r is refused" % bad)
    for good in ("Ikaruga", "Ikaruga (Japan)", "Rez (USA) (Rev 1)"):
        check(rx is not None and rx.match(good), "name %r is allowed" % good)


if __name__ == "__main__":
    main(globals())
