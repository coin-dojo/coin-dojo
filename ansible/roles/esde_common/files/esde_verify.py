#!/usr/bin/env python3
"""Check what ES-DE will actually show after a deploy (ticket #89; stdlib only, runs on the cabinet).

    esde_verify.py counts --es-systems FILE --gamelists DIR [--nested-dir DIR] [--expect JSON] [--json]
    esde_verify.py log --log es_log.txt [--prev es_log.txt.bak] [--ignore REGEX ...] [--warn-only]

counts: for every <system> in es_systems.xml, the set of <game><path> entries in DIR/<name>/gamelist.xml
  (the gamelist ES-DE reads) must equal the set of symlinks under the system's <path> (or
  --nested-dir/<name>). --expect '{"must-play": 669, ...}' also FAILs a system whose link count differs
  from the count the deploy built (the neogeo bug: 1035 shown instead of 1143). --json prints only
  {"name": links, ...} (for --expect of a later run) and still exits 1 on a mismatch.
log: ES-DE `Error:` lines in --log that are not in --prev (timestamps stripped) FAIL; new `Warning:`
  lines are reported as WARN. --warn-only reports new errors as WARN (no restart happened this run).

Output: one line per system / problem, then `RESULT: PASS` (exit 0) or `RESULT: FAIL` (exit 1).
Exit 2 = usage error, 3 = input error (missing or malformed file; also prints `RESULT: FAIL`).
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INPUT = 0, 1, 2, 3
# "Nov 14 22:30:01 Error: ..." (ES-DE 3.x) or "[2026-11-14 22:30:01.123] Error: ...".
STAMP_RX = re.compile(r"^\s*(?:\[[^\]]*\]|[A-Z][a-z]{2} +\d{1,2} +\d\d:\d\d:\d\d(?:\.\d+)?)\s*")
ERROR_RX = re.compile(r"\bError:")
WARN_RX = re.compile(r"\bWarn(?:ing)?:")


class InputError(Exception):
    pass


def parse_xml(path):
    """ES-DE files may have a second root (<alternativeEmulator> + <gameList>): parse them wrapped."""
    try:
        with open(path, encoding="utf-8") as f:
            text = re.sub(r"^\s*<\?xml[^>]*\?>", "", f.read()).strip()
        return ET.fromstring("<wrap>" + text + "</wrap>")
    except OSError as e:
        raise InputError("cannot read %s: %s" % (path, e))
    except ET.ParseError as e:
        raise InputError("malformed XML in %s: %s" % (path, e))


def read_systems(path):
    out = []
    for s in parse_xml(path).iter("system"):
        name = (s.findtext("name") or "").strip()
        if name:
            out.append((name, (s.findtext("path") or "").strip()))
    if not out:
        raise InputError("%s: no <system> entries" % path)
    return out


def game_paths(gamelist):
    root = parse_xml(gamelist)
    return set(norm((g.findtext("path") or "").strip()) for g in root.iter("game"))


def norm(p):
    """'./a/b.zip', 'a/b.zip' -> './a/b.zip' (ES-DE gamelist path form)."""
    return "./" + os.path.normpath(p)


def link_paths(top):
    out = set()
    for d, dirs, files in os.walk(top):
        for n in dirs + files:
            p = os.path.join(d, n)
            if os.path.islink(p):
                out.add(norm(os.path.relpath(p, top)))
    return out


def cmd_counts(a):
    try:
        expect = json.loads(a.expect) if a.expect else {}
        if not isinstance(expect, dict):
            raise ValueError("not an object")
    except ValueError as e:
        raise InputError("--expect: %s" % e)
    problems, counts = [], {}
    for name, path in read_systems(a.es_systems):
        top = os.path.join(a.nested_dir, name) if a.nested_dir else path
        if not os.path.isdir(top):
            problems.append("%s: roms dir %s is missing" % (name, top))
            continue
        gl = os.path.join(a.gamelists, name, "gamelist.xml")
        if not os.path.isfile(gl):
            problems.append("%s: gamelist %s is missing" % (name, gl))
            continue
        games, links = game_paths(gl), link_paths(top)
        counts[name] = len(links)
        if not a.json:
            print("  %-10s links %5d  games %5d" % (name, len(links), len(games)))
        if games != links:
            extra, missing = sorted(links - games), sorted(games - links)
            problems.append("%s: %d link(s) without a game entry %s, %d game(s) without a link %s"
                            % (name, len(extra), extra[:3], len(missing), missing[:3]))
        if name in expect and int(expect[name]) != len(links):
            problems.append("%s: the deploy built %s game(s), the cabinet has %d" % (name, expect[name], len(links)))
    for name in sorted(set(expect) - set(counts)):
        if not any(p.startswith(name + ":") for p in problems):
            problems.append("%s: built by the deploy but not in %s" % (name, a.es_systems))
    for p in problems:
        print("FAIL " + p)
    if a.json:
        print(json.dumps(counts, sort_keys=True))
    print("RESULT: FAIL" if problems else "RESULT: PASS")
    return EXIT_FAIL if problems else EXIT_OK


def log_lines(path, rx, ignore):
    if not path or not os.path.isfile(path):
        return set()
    out = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = STAMP_RX.sub("", line.rstrip("\n")).strip()
            if rx.search(line) and not any(i.search(line) for i in ignore):
                out.add(line)
    return out


def cmd_log(a):
    if not os.path.isfile(a.log):
        raise InputError("log %s is missing" % a.log)
    try:
        ignore = [re.compile(r) for r in a.ignore]
    except re.error as e:
        raise InputError("--ignore: %s" % e)
    errors = sorted(log_lines(a.log, ERROR_RX, ignore) - log_lines(a.prev, ERROR_RX, ignore))
    warns = sorted(log_lines(a.log, WARN_RX, ignore) - log_lines(a.prev, WARN_RX, ignore))
    for w in warns:
        print("WARN new ES-DE warning: " + w)
    for e in errors:
        print(("WARN" if a.warn_only else "FAIL") + " new ES-DE error: " + e)
    print("log: %d new error(s), %d new warning(s)" % (len(errors), len(warns)))
    bad = errors and not a.warn_only
    print("RESULT: FAIL" if bad else "RESULT: PASS")
    return EXIT_FAIL if bad else EXIT_OK


def make_parser():
    p = argparse.ArgumentParser(prog="esde_verify.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("counts", help="links == gamelist games per virtual system (+ expected counts)")
    c.add_argument("--es-systems", required=True)
    c.add_argument("--gamelists", required=True)
    c.add_argument("--nested-dir", help="roms_nested dir to use instead of each system's <path>")
    c.add_argument("--expect", help='JSON {"system": link count} the deploy built')
    c.add_argument("--json", action="store_true", help="print only the counts as JSON (+ FAIL/RESULT)")
    c.set_defaults(fn=cmd_counts)
    g = sub.add_parser("log", help="new ES-DE Error lines since the previous session")
    g.add_argument("--log", required=True)
    g.add_argument("--prev")
    g.add_argument("--ignore", action="append", default=[])
    g.add_argument("--warn-only", action="store_true")
    g.set_defaults(fn=cmd_log)
    return p


def main(argv=None):
    try:
        a = make_parser().parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK
    try:
        return a.fn(a)
    except InputError as e:
        print("FAIL input: %s" % e)
        print("RESULT: FAIL")
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
