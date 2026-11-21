#!/usr/bin/env python3
"""Validate a `collgen.py nested` output tree before it goes to the cabinet (stdlib only).

    python3 skills/collections/validate_nested.py --out NESTED_OUT [--es-systems FILE] [--map FROM=TO ...]

Checks, per virtual system in es_systems.xml (default NESTED_OUT/custom_systems/es_systems.xml):
  1. hidden name: on an arcade/neogeo platform, no folder and no linked file under roms_nested/<system>/ may
     have a stem that is a MAME BIOS/device name (ES-DE drops those silently: the `neogeo` folder bug).
  2. dead link: every symlink under roms_nested/ and downloaded_media/ resolves to an existing file.
     Links point at cabinet paths (/data/roms, /data/media); off the cabinet pass --map /data/roms=LOCAL_DIR.
  3. merge (#116): no two links of one virtual system may resolve to the same file. ES-DE matches a played game's
     gamelist entry by getCanonicalPath() (symlinks resolved), so two links to one ROM make it drop one folder's entry.
     A crossover game links through a pointer file (nested.POINTER_DIR); each pointer must name an existing ROM.
  4. count: the set of <game><path> entries in gamelists/<system>/gamelist.xml equals the set of symlinks
     under roms_nested/<system>/ (so link count == game count; hidden games keep their link, so they count).
  5. shell (#202): no shell-active character (` $ " backslash, nested.SHELL_ACTIVE) in a system's name, fullname
     or path, in a gamelist <game>/<folder> <path> or <name>, or in a file/folder name under roms_nested/<system>/.
     ES-DE 3.5.0 hands these to event scripts through sh -c inside double quotes (Scripting.cpp:67-100).
Prints one line per problem, a summary per system, then `RESULT: PASS` (exit 0) or `RESULT: FAIL` (exit 1).
Exit 2 = usage error, 3 = input error (missing dir / es_systems.xml, malformed XML, bad name list).
Called by CI (tests) and by the Ansible deploy as a pre-write gate.
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nested  # noqa: E402  (next to this file)

HIDING_PLATFORMS = ("arcade", "neogeo")     # ES-DE PlatformIds ARCADE / SNK_NEO_GEO (FileData::isArcadeAsset)
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INPUT = 0, 1, 2, 3


class InputError(Exception):
    pass


def parse_xml(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = re.sub(r"^\s*<\?xml[^>]*\?>", "", f.read()).strip()
        return ET.fromstring("<wrap>" + text + "</wrap>")
    except OSError as e:
        raise InputError("cannot read %s: %s" % (path, e))
    except ET.ParseError as e:
        raise InputError("malformed XML in %s: %s" % (path, e))


def read_systems(path):
    """es_systems.xml -> [(name, set of platform ids)]."""
    root = parse_xml(path)
    out = []
    for s in root.iter("system"):
        name = (s.findtext("name") or "").strip()
        plats = set(p for p in re.split(r"[,\s]+", (s.findtext("platform") or "").strip().lower()) if p)
        if name:
            out.append((name, plats))
    if not out:
        raise InputError("%s: no <system> entries" % path)
    return out


def remap(target, maps):
    for src, dst in maps:
        if target == src or target.startswith(src.rstrip("/") + "/"):
            return dst.rstrip("/") + target[len(src.rstrip("/")):]
    return target


def walk_links(root):
    """-> [(path relative to root, absolute link path)] for every symlink under root (dirs not followed)."""
    out = []
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            p = os.path.join(dp, n)
            if os.path.islink(p):
                out.append((os.path.relpath(p, root), p))
    return sorted(out)


def shell_active(value):
    """The shell-active characters in value (sorted, no repeats), '' when it is safe (#202)."""
    return "".join(sorted(set(c for c in value or "" if c in nested.SHELL_ACTIVE)))


def check_shell(out, es_systems):
    """Check 5 (#202): every field ES-DE passes to an event script, per virtual system. -> [problem lines]."""
    problems = []
    why = "ES-DE runs event scripts via sh -c (#202)"
    for s in parse_xml(es_systems).iter("system"):
        name = (s.findtext("name") or "").strip()
        for tag in ("name", "fullname", "path"):
            v = (s.findtext(tag) or "").strip()
            if shell_active(v):
                problems.append("shell: %s <system> <%s> %r has %s: %s" % (es_systems, tag, v, shell_active(v), why))
        if not name or shell_active(name):
            continue
        gl = os.path.join(out, "gamelists", name, "gamelist.xml")
        if os.path.isfile(gl):
            for e in parse_xml(gl).iter():
                if e.tag not in ("game", "folder"):
                    continue
                for tag in ("path", "name"):
                    v = (e.findtext(tag) or "").strip()
                    if shell_active(v):
                        problems.append("shell: %s <%s> <%s> %r has %s: %s"
                                        % (os.path.relpath(gl, out), e.tag, tag, v, shell_active(v), why))
        sdir = os.path.join(out, "roms_nested", name)
        for dp, dns, fns in os.walk(sdir):
            for n in sorted(dns + fns):
                if shell_active(n):
                    problems.append("shell: %s has %s: %s" % (os.path.relpath(os.path.join(dp, n), out),
                                                              shell_active(n), why))
    return problems


def check(out, es_systems, maps, hidden):
    problems, summary = [], []
    roms = os.path.join(out, "roms_nested")
    for name, plats in read_systems(es_systems):
        sdir = os.path.join(roms, name)
        if not os.path.isdir(sdir):
            problems.append("%s: missing dir %s" % (name, sdir))
            continue
        hides = bool(plats & set(HIDING_PLATFORMS))
        if hides:
            for dp, dns, fns in os.walk(sdir):
                for n in dns:
                    kind = hidden.get(n.lower())
                    if kind:
                        problems.append("%s: hidden name: folder %s is a MAME %s name (ES-DE drops it)"
                                        % (name, os.path.relpath(os.path.join(dp, n), roms), kind))
                for n in fns:
                    kind = hidden.get(os.path.splitext(n)[0].lower())
                    if kind:
                        problems.append("%s: hidden name: file %s is a MAME %s name (ES-DE drops it)"
                                        % (name, os.path.relpath(os.path.join(dp, n), roms), kind))
        links = [(rel, p) for rel, p in walk_links(sdir)]
        gl = os.path.join(out, "gamelists", name, "gamelist.xml")
        if not os.path.isfile(gl):
            problems.append("%s: missing gamelist %s" % (name, gl))
            continue
        games = parse_xml(gl).findall(".//game")
        paths = set()
        hidden_games = 0
        for g in games:
            p = (g.findtext("path") or "").strip()
            paths.add(os.path.normpath(p[2:] if p.startswith("./") else p))
            if (g.findtext("hidden") or "").strip().lower() == "true":
                hidden_games += 1
        lset = set(rel for rel, _p in links)
        canon = {}
        for rel, p in links:
            target = os.readlink(p)
            if not os.path.isabs(target):
                target = os.path.join(os.path.dirname(p), target)
            canon.setdefault(os.path.realpath(remap(target, maps)), []).append(rel)
        for real, rels in sorted(canon.items()):
            if len(rels) > 1:
                problems.append("%s: merge: %d links resolve to the same file %s (%s): ES-DE would merge their "
                                "gamelist entries (#116)" % (name, len(rels), real, ", ".join(sorted(rels)[:3])))
        if len(games) != len(lset) or paths != lset:
            extra, missing = sorted(lset - paths), sorted(paths - lset)
            problems.append("%s: count: %d link(s) but %d <game> entries (%d link(s) without an entry%s, "
                            "%d entr(y/ies) without a link%s)"
                            % (name, len(lset), len(games), len(extra), (": " + ", ".join(extra[:3])) if extra else "",
                               len(missing), (": " + ", ".join(missing[:3])) if missing else ""))
        folders = sum(1 for f in os.listdir(sdir) if os.path.isdir(os.path.join(sdir, f)))
        summary.append("  %-10s platform=%-7s %2d folder(s) %5d link(s) %5d game(s) %3d hidden"
                       % (name, ",".join(sorted(plats)) or "-", folders, len(lset), len(games), hidden_games))
    pdir = os.path.join(roms, nested.POINTER_DIR)
    for dp, _dn, fns in os.walk(pdir):
        for n in sorted(fns):
            p = os.path.join(dp, n)
            with open(p, encoding="utf-8", errors="replace") as f:
                first = f.readline()
            target = first[len(nested.POINTER_MAGIC):].strip() if first.startswith(nested.POINTER_MAGIC) else ""
            if not target or not os.path.exists(remap(target, maps)):
                problems.append("dead pointer: %s -> %r" % (os.path.relpath(p, roms), target or first.strip()))
    dead = 0
    for sub in ("roms_nested", "downloaded_media"):
        root = os.path.join(out, sub)
        if not os.path.isdir(root):
            continue
        for rel, p in walk_links(root):
            target = os.readlink(p)
            if not os.path.isabs(target):
                target = os.path.join(os.path.dirname(p), target)
            if not os.path.exists(remap(target, maps)):
                dead += 1
                if dead <= 20:
                    problems.append("dead link: %s/%s -> %s" % (sub, rel, target))
    if dead > 20:
        problems.append("dead link: ... %d more" % (dead - 20))
    problems += check_shell(out, es_systems)
    return problems, summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate_nested.py", description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="collgen.py nested output dir (custom_systems/, gamelists/, roms_nested/)")
    ap.add_argument("--es-systems", help="es_systems.xml to read (default OUT/custom_systems/es_systems.xml)")
    ap.add_argument("--map", action="append", default=[], metavar="FROM=TO",
                    help="check link targets under FROM at TO instead (e.g. /data/roms=$W/roms); repeatable")
    ap.add_argument("--names", default=nested.HIDDEN_NAMES_TSV, help="MAME BIOS/device list (default mame_hidden_names.tsv)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK
    maps = []
    for m in args.map:
        if "=" not in m or not m.split("=", 1)[0]:
            print("validate_nested.py: --map wants FROM=TO, got %r" % m, file=sys.stderr)
            return EXIT_USAGE
        maps.append(tuple(m.split("=", 1)))
    try:
        if not os.path.isdir(args.out):
            raise InputError("--out is not a directory: %s" % args.out)
        try:
            hidden = nested.load_hidden_names(args.names)
        except (OSError, ValueError) as e:
            raise InputError("--names: %s" % e)
        es = args.es_systems or os.path.join(args.out, "custom_systems", "es_systems.xml")
        problems, summary = check(args.out, es, maps, hidden)
    except InputError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        print("RESULT: FAIL")
        return EXIT_INPUT
    print("virtual systems:")
    for line in summary:
        print(line)
    for p in problems:
        print("FAIL " + p)
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return EXIT_FAIL if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
