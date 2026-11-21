#!/usr/bin/env python3
"""Which edited gamelists may go to the cabinet, and did the cabinet change since the pull? (#89; stdlib)

    gamelist_drift.py --workspace W --live LIVE.md5|-

W is the laptop workspace of `make pull-gamelists W=...`: W/gamelists/<system>/gamelist.xml (the pulled
copies, edited there by collgen names/dupes/metadata or by hand) and W/pulled.md5 (md5sum lines
`<md5>  <system>/gamelist.xml` of the cabinet's files at pull time). --live is the same md5sum listing
taken from the cabinet now (`-` = stdin).

Per workspace system (the virtual ones, env HK_VIRTUAL_SYSTEMS = hk_virtual_systems, are skipped: they are
built by the deploy, never copied):
  same     workspace file == live file           -> nothing to copy
  changed  differs, and live == pulled            -> copy it
  new      no live file and none at pull time     -> copy it (a new system)
  drift    differs, and live != pulled (or the file vanished / was never pulled) -> ABORT, nothing copied:
           ES-DE or someone changed the cabinet's file after the pull; pull again and redo the edit.
Prints one JSON object {"changed": [...], "new": [...], "same": [...], "drift": [...], "skipped": [...]}
plus `RESULT: PASS` (exit 0) or `RESULT: FAIL` (exit 1, any drift). Exit 2 usage, 3 input error
(no gamelists dir, no pulled.md5, malformed md5 line).
"""
import argparse
import hashlib
import json
import os
import re
import sys

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INPUT = 0, 1, 2, 3
VIRTUAL = tuple(json.loads(os.environ.get("HK_VIRTUAL_SYSTEMS") or open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "virtual_systems.json")).read()))     # hk_virtual_systems (#94): env (the role), else the file beside this script
LINE_RX = re.compile(r"^([0-9a-f]{32}) [ *](\S+)/gamelist\.xml$")


class InputError(Exception):
    pass


def parse_md5(text, where):
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        m = LINE_RX.match(line)
        if not m:
            raise InputError("%s line %d is not '<md5>  <system>/gamelist.xml': %r" % (where, n, line))
        out[m.group(2).lstrip("./")] = m.group(1)
    return out


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(workspace, live_text):
    gdir = os.path.join(workspace, "gamelists")
    if not os.path.isdir(gdir):
        raise InputError("no %s (run make pull-gamelists W=%s first)" % (gdir, workspace))
    pulled_file = os.path.join(workspace, "pulled.md5")
    try:
        with open(pulled_file, encoding="utf-8") as f:
            pulled = parse_md5(f.read(), pulled_file)
    except OSError as e:
        raise InputError("cannot read %s: %s" % (pulled_file, e))
    live = parse_md5(live_text, "--live")
    res = {"changed": [], "new": [], "same": [], "drift": [], "skipped": []}
    for system in sorted(os.listdir(gdir)):
        f = os.path.join(gdir, system, "gamelist.xml")
        if not os.path.isfile(f):
            continue
        if system in VIRTUAL:
            res["skipped"].append(system)
            continue
        local = md5(f)
        if live.get(system) == local:
            res["same"].append(system)
        elif system not in live and system not in pulled:
            res["new"].append(system)
        elif system in live and live[system] == pulled.get(system):
            res["changed"].append(system)
        elif system not in live:
            res["drift"].append("%s: gone from the cabinet since the pull" % system)
        elif system not in pulled:
            res["drift"].append("%s: on the cabinet but not in pulled.md5 (pulled before it existed?)" % system)
        else:
            res["drift"].append("%s: the cabinet's file changed since the pull (live %s, pulled %s)"
                                % (system, live[system][:8], pulled[system][:8]))
    return res


def main(argv=None):
    p = argparse.ArgumentParser(prog="gamelist_drift.py", description=__doc__.split("\n")[0])
    p.add_argument("--workspace", required=True)
    p.add_argument("--live", required=True, help="md5sum listing from the cabinet, or - for stdin")
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK
    try:
        if a.live == "-":
            live_text = sys.stdin.read()
        else:
            with open(a.live, encoding="utf-8") as f:
                live_text = f.read()
        res = plan(a.workspace, live_text)
    except (InputError, OSError) as e:
        print("FAIL input: %s" % e)
        print("RESULT: FAIL")
        return EXIT_INPUT
    print(json.dumps(res, sort_keys=True))
    for d in res["drift"]:
        print("FAIL drift: " + d)
    print("RESULT: FAIL" if res["drift"] else "RESULT: PASS")
    return EXIT_FAIL if res["drift"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
