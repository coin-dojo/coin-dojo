#!/usr/bin/env python3
"""Tests for gamelist_drift.py (#89): which workspace gamelists may go, abort on cabinet drift. Plain python3.

    python3 ansible/roles/esde_collections/tests/test_gamelist_drift.py

Broken cases (must FAIL): the cabinet's file changed after the pull, vanished, or was never pulled;
no pulled.md5, a malformed md5 line, no gamelists dir (exit 3); bad usage (exit 2).
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "files"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "..", "skills", "collections"))
import nested  # noqa: E402  (the one source of the virtual-system names, #94)
os.environ["HK_VIRTUAL_SYSTEMS"] = json.dumps(list(nested.ALL_VIRTUAL))     # the role sets this for the real run
import gamelist_drift as gd  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def h(text):
    return hashlib.md5(text.encode()).hexdigest()


def ws(files, pulled):
    """Workspace with gamelists/<system>/gamelist.xml = files[system] and pulled.md5 from {system: text}."""
    d = tempfile.mkdtemp()
    for s, text in files.items():
        os.makedirs(os.path.join(d, "gamelists", s))
        with open(os.path.join(d, "gamelists", s, "gamelist.xml"), "w") as f:
            f.write(text)
    with open(os.path.join(d, "pulled.md5"), "w") as f:
        for s, text in pulled.items():
            f.write("%s  %s/gamelist.xml\n" % (h(text), s))
    return d


def live(m):
    return "".join("%s  %s/gamelist.xml\n" % (h(t), s) for s, t in m.items())


def run(workspace, live_text):
    p = os.path.join(workspace, "live.md5")
    with open(p, "w") as f:
        f.write(live_text)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = gd.main(["--workspace", workspace, "--live", p])
    o = out.getvalue()
    js = json.loads(o.splitlines()[0]) if o.startswith("{") else None
    return rc, o, js


def test_plan():
    old = {"cps2": "A", "fbneo": "B", "nes": "C"}
    d = ws({"cps2": "A", "fbneo": "B2", "snes": "new", "must-play": "virtual"}, old)
    rc, out, js = run(d, live(old))
    check(rc == 0 and "RESULT: PASS" in out, "no drift passes")
    check(js["same"] == ["cps2"] and js["changed"] == ["fbneo"] and js["new"] == ["snes"] and js["skipped"] == ["must-play"],
          "same / changed / new / virtual skipped %s" % js)
    shutil.rmtree(d)


def test_broken_drift():
    pulled = {"cps2": "A", "fbneo": "B"}
    d = ws({"cps2": "A2", "fbneo": "B2"}, pulled)
    rc, out, js = run(d, live({"cps2": "A-changed-by-es-de", "fbneo": "B"}))
    check(rc == 1 and js["drift"] and "cps2: the cabinet's file changed since the pull" in out and js["changed"] == ["fbneo"],
          "BROKEN: live changed after the pull -> drift, exit 1")
    rc, out, js = run(d, live({"fbneo": "B"}))
    check(rc == 1 and "cps2: gone from the cabinet since the pull" in out, "BROKEN: live file vanished -> drift")
    shutil.rmtree(d)
    d = ws({"cps2": "A2"}, {})
    rc, out, js = run(d, live({"cps2": "A"}))
    check(rc == 1 and "not in pulled.md5" in out, "BROKEN: never pulled but on the cabinet -> drift")
    shutil.rmtree(d)


def test_broken_inputs():
    d = ws({"cps2": "A"}, {"cps2": "A"})
    rc, out, _ = run(d, "garbage line\n")
    check(rc == 3 and "RESULT: FAIL" in out, "BROKEN: malformed live md5 line exits 3")
    os.remove(os.path.join(d, "pulled.md5"))
    rc, out, _ = run(d, live({"cps2": "A"}))
    check(rc == 3 and "pulled.md5" in out, "BROKEN: no pulled.md5 exits 3")
    shutil.rmtree(os.path.join(d, "gamelists"))
    rc, out, _ = run(d, "")
    check(rc == 3 and "make pull-gamelists" in out, "BROKEN: no gamelists dir exits 3")
    shutil.rmtree(d)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = gd.main(["--workspace", "x"])
    check(rc == 2, "BROKEN: bad usage exits 2")


if __name__ == "__main__":
    main(globals())
