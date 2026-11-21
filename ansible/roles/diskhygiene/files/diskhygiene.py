#!/usr/bin/env python3
"""diskhygiene (#91): root-fs cleanups for the Debian cabinet, run by the diskhygiene role.

Two subcommands, both read-only unless --apply (prune) or a later apt-get step (purge):

  prune      Files under /usr/share that the dpkg path-exclude rules leave off future installs, but
             that are already on disk (docs, man, info, lintian, non-English locales). Without
             --apply it only measures. Rules are given in dpkg order (last matching rule wins).
               diskhygiene.py prune --exclude '/usr/share/doc/*' --include '/usr/share/doc/*/copyright' [--apply]
  purge-plan Simulates `apt-get -s purge` for each package group and prints a JSON plan. Exit 2 when
             a group names a protected package, an approved group would cascade (apt would remove
             more than the list says), or an approved group is unknown. Never removes anything.
               diskhygiene.py purge-plan --protect 'mesa*' --group toolchain=gcc,g++ --enable toolchain

Pure stdlib, Python 3.9+. Output: one JSON object on stdout.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

PRUNE_PREFIX = "/usr/share/"
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
GLOB_CHARS = "*?["


# ---- prune ------------------------------------------------------------------------------------
def glob_base(glob):
    """The fixed directory a glob starts in: '/usr/share/doc/*' -> '/usr/share/doc'."""
    cut = min([glob.find(c) for c in GLOB_CHARS if c in glob] or [len(glob)])
    head = glob[:cut]
    return head.rstrip("/") if head.endswith("/") else os.path.dirname(head)


def check_rules(rules):
    """Problems with a rule list [(kind, glob)]; empty = ok. Excludes must stay inside one
    /usr/share/<dir> (never /usr/share itself, /usr/lib, /etc, /)."""
    probs = []
    if not any(k == "exclude" for k, _ in rules):
        probs.append("no --exclude rule")
    for kind, glob in rules:
        if kind not in ("exclude", "include"):
            probs.append("unknown rule kind %r" % kind)
        if not glob.startswith("/"):
            probs.append("rule %r is not an absolute path" % glob)
        if "/../" in glob + "/" or "/./" in glob + "/":
            probs.append("rule %r has a . or .. segment" % glob)
        if kind == "exclude":
            base = glob_base(glob)
            if not (base + "/").startswith(PRUNE_PREFIX) or base.count("/") < 3:
                probs.append("exclude %r reaches outside one %s<dir>" % (glob, PRUNE_PREFIX))
    return probs


def is_excluded(path, rules):
    """dpkg semantics: every rule is tested in order, the last match wins; no match = kept."""
    state = False
    for kind, glob in rules:
        if fnmatch.fnmatchcase(path, glob):
            state = kind == "exclude"
    return state


def prune(rules, root="", apply=False):
    """Walk each exclude's base dir under `root` (a test tree; "" = the real /). Returns counts."""
    files = size = dirs = 0
    bases = sorted({glob_base(g) for k, g in rules if k == "exclude"})
    for base in bases:
        top = root + base
        if not os.path.isdir(top) or os.path.islink(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top, topdown=False):
            logical_dir = dirpath[len(root):] if root else dirpath
            # Symlinks to dirs are listed in dirnames and never followed: treat them as files.
            leaves = filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
            for name in leaves:
                real = os.path.join(dirpath, name)
                if not is_excluded(logical_dir + "/" + name, rules):
                    continue
                st = os.lstat(real)
                files += 1
                size += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                if apply:
                    os.unlink(real)
            # an empty dir goes when it is excluded itself, or when everything inside it would be (doc/<pkg>/ under
            # doc/*/*, #293)
            gone = is_excluded(logical_dir, rules) or is_excluded(logical_dir + "/_", rules)
            if apply and dirpath != top and gone and not os.listdir(dirpath):
                os.rmdir(dirpath)
                dirs += 1
    return {"files": files, "bytes": size, "mb": round(size / 1048576, 1), "dirs_removed": dirs,
            "applied": apply}


# ---- purge-plan -------------------------------------------------------------------------------
def parse_sim(text):
    """Package names apt-get -s would purge/remove: 'Purg gcc [4:14.2.0-1]', 'Remv libx:arm64 [..]'."""
    out = []
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[0] in ("Purg", "Remv"):
            out.append(parts[1].split(":", 1)[0])
    return out


def plan_group(name, requested, installed_sizes, simulated, protect, enabled):
    """Pure: one group's plan. installed_sizes = {pkg: KiB} of installed packages."""
    bad_names = [p for p in requested if not PKG_RE.match(p)]
    todo = [p for p in requested if p in installed_sizes]
    sim = list(dict.fromkeys(simulated))
    cascade = sorted(set(sim) - set(todo))
    def hits(names):
        return sorted({p for p in names if any(fnmatch.fnmatchcase(p, g) for g in protect)})
    named = hits(requested)          # the list itself names a protected package: a config bug
    protected = hits(list(requested) + sim)
    errors = []
    if bad_names:
        errors.append("invalid package names: %s" % ", ".join(bad_names))
    if protected:
        errors.append("protected packages: %s" % ", ".join(protected))
    if cascade:
        errors.append("apt would also remove: %s" % ", ".join(cascade))
    kib = sum(installed_sizes.get(p, 0) for p in (sim or todo))
    return {"group": name, "enabled": enabled, "installed": todo, "simulated": sim,
            "cascade": cascade, "protected": protected, "freed_mb": round(kib / 1024, 1),
            "errors": errors,
            # A protected name in the list is a config bug: fail even when the group is not approved.
            # A cascade only fails an approved group (an unapproved one is just reported).
            "fatal": bool(named or bad_names or (enabled and errors))}


def plan(groups, enable, protect, installed_sizes, simulate):
    """groups = {name: [pkgs]}, simulate(pkgs) -> apt-get -s text. Returns (plan, exit code)."""
    out = {"groups": [], "purge": [], "freed_mb": 0.0, "freed_mb_all_groups": 0.0, "errors": []}
    for e in enable:
        if e not in groups:
            out["errors"].append("approved group %r is not defined" % e)
    for name, pkgs in groups.items():
        todo = [p for p in pkgs if p in installed_sizes and PKG_RE.match(p)]
        sim = parse_sim(simulate(todo)) if todo else []
        g = plan_group(name, pkgs, installed_sizes, sim, protect, name in enable)
        out["groups"].append(g)
        out["freed_mb_all_groups"] += g["freed_mb"]
        if g["fatal"]:
            out["errors"].extend("%s: %s" % (name, e) for e in g["errors"])
        elif g["enabled"]:
            out["purge"].extend(p for p in g["installed"] if p not in out["purge"])
            out["freed_mb"] += g["freed_mb"]
    out["freed_mb"] = round(out["freed_mb"], 1)
    out["freed_mb_all_groups"] = round(out["freed_mb_all_groups"], 1)
    return out, (2 if out["errors"] else 0)


def installed_packages():
    """{pkg: Installed-Size KiB} for every installed package (dpkg-query; read-only)."""
    res = subprocess.run(["dpkg-query", "-W", "-f", "${db:Status-Abbrev}\t${Package}\t${Installed-Size}\n"],
                         capture_output=True, text=True, check=True)
    out = {}
    for ln in res.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3 and parts[0][1:2] == "i":
            out[parts[1]] = int(parts[2] or 0)
    return out


def apt_simulate(pkgs):
    res = subprocess.run(["apt-get", "-s", "-o", "Debug::NoLocking=1", "purge"] + list(pkgs),
                         capture_output=True, text=True, env=dict(os.environ, LC_ALL="C"))
    if res.returncode != 0:
        raise RuntimeError("apt-get -s purge failed: %s" % res.stderr.strip()[-300:])
    return res.stdout


# ---- cli --------------------------------------------------------------------------------------
class _Rule(argparse.Action):
    def __call__(self, parser, ns, value, option_string=None):
        ns.rules.append((self.dest, value))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prune")
    p.set_defaults(rules=[])
    p.add_argument("--exclude", action=_Rule, default=argparse.SUPPRESS)
    p.add_argument("--include", action=_Rule, default=argparse.SUPPRESS)
    p.add_argument("--root", default="", help="test tree prefix (default: the real /)")
    p.add_argument("--apply", action="store_true")
    q = sub.add_parser("purge-plan")
    q.add_argument("--protect", action="append", default=[])
    q.add_argument("--group", action="append", default=[], help="name=pkg1,pkg2")
    q.add_argument("--enable", action="append", default=[])
    a = ap.parse_args(argv)

    if a.cmd == "prune":
        probs = check_rules(a.rules)
        if probs:
            print(json.dumps({"errors": probs}))
            return 2
        print(json.dumps(prune(a.rules, root=a.root.rstrip("/"), apply=a.apply)))
        return 0

    groups = {}
    for spec in a.group:
        name, _, pkgs = spec.partition("=")
        groups[name] = [x for x in pkgs.split(",") if x]
    enable = [e for e in a.enable if e]
    try:
        out, rc = plan(groups, enable, a.protect, installed_packages(), apt_simulate)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"errors": [str(exc)], "purge": []}))
        return 2
    print(json.dumps(out))
    return rc


if __name__ == "__main__":
    sys.exit(main())
