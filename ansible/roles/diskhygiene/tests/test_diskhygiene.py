#!/usr/bin/env python3
"""Tests for the `diskhygiene` role (#91): dpkg path-exclude, prune, planned package purges.

Pure stdlib, pytest-compatible, or run directly:
    python3 ansible/roles/diskhygiene/tests/test_diskhygiene.py

Broken cases (test_broken_*) MUST be flagged: a removal list naming a package the stack needs, a
cascading purge, an exclude reaching outside /usr/share/<dir>, writes not gated on check mode.
"""
import fnmatch
import importlib.util
import os
import re
import shutil
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)

_spec = importlib.util.spec_from_file_location("diskhygiene", os.path.join(ROLE, "files", "diskhygiene.py"))
dh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dh)


def _read(*parts, base=ROLE):
    with open(os.path.join(base, *parts), encoding="utf-8") as fh:
        return fh.read()


def _defaults():
    """Tiny parser for this role's defaults: scalars, `[a, b]` lists, block lists, and one level of
    dict-of-block-lists (diskhygiene_purge_groups). Comments and blank lines are skipped."""
    out, key, sub = {}, None, None
    for raw in _read("defaults", "main.yml").splitlines():
        ln = raw.split(" #")[0].rstrip()
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\w+):\s*(.*)$", ln)
        if m:
            key, sub, val = m.group(1), None, m.group(2).strip()
            if val.startswith("["):
                out[key] = [x.strip() for x in val.strip("[]").split(",") if x.strip()]
            elif val:
                out[key] = val.strip('"')
            else:
                out[key] = None
            continue
        m = re.match(r"^  (\w+):\s*$", ln)
        if m and key:
            out[key] = out[key] or {}
            sub = m.group(1)
            out[key][sub] = []
            continue
        m = re.match(r"^\s+- (.+)$", ln)
        if m and key:
            if sub is not None:
                out[key][sub].append(m.group(1).strip())
            else:
                out[key] = (out[key] or []) + [m.group(1).strip()]
    return out


def _rules(d):
    return [("exclude", g) for g in d["diskhygiene_path_excludes"]] + \
           [("include", g) for g in d["diskhygiene_path_includes"]]


def _render(d):
    """Render the dpkg drop-in template (only its two for-loops)."""
    out = []
    for ln in _read("templates", "dpkg-diskhygiene.j2").splitlines():
        m = re.match(r"^\{% for g in (\w+) %\}$", ln)
        if m:
            loop = d[m.group(1)]
            continue
        if ln == "{% endfor %}":
            continue
        if "{{ g }}" in ln:
            out.extend(ln.replace("{{ g }}", g) for g in loop)
        else:
            out.append(ln)
    return "\n".join(out) + "\n"


def _strip_comments(text):
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


STACK = ["retroarch", "cage", "libwayland-client0", "libwayland-server0", "mesa-vulkan-drivers",
         "mesa-libgallium", "python3", "openssh-server", "sudo", "systemd", "libretro-core-info",
         "libsdl2-2.0-0", "linux-image-6.18.50+rpt-rpi-v8", "firmware-brcm80211", "rpi-swap"]


def rule_tasks(tasks):
    """Problems in tasks/main.yml text (comments stripped)."""
    t = _strip_comments(tasks)
    p = []
    blocks = re.split(r"(?m)^- name: ", t)[1:]
    for b in blocks:
        title = b.splitlines()[0]
        writes = re.search(r"become: true", b) is not None
        if writes and "not ansible_check_mode" not in b:
            p.append("write task %r not gated on 'not ansible_check_mode'" % title)
        if "ansible.builtin.script" in b and "--apply" not in b and not writes:
            if "check_mode: false" not in b:
                p.append("read-only script %r lacks check_mode: false (dry run would skip it)" % title)
            if "become" in b:
                p.append("read-only script %r uses become (dry run must need no sudo)" % title)
    if re.search(r"autoremove|auto-remove", t):
        p.append("autoremove used (purge lists must be explicit)")
    if re.search(r"\b(reboot|poweroff|state:\s*restarted|systemctl\s+restart)\b", t):
        p.append("role reboots or restarts something")
    if "diskhygiene_plan.purge" not in t or "['apt-get', '-y', 'purge']" not in t:
        p.append("purge does not run exactly the planned list")
    if "ansible.builtin.apt:" in t:
        p.append("purge via the apt module (bypasses the apt-get -s plan)")
    return p


# ---- defaults, template, wiring ---------------------------------------------------------------
def test_dpkg_cfg_name_is_read_by_dpkg():
    name = os.path.basename(_defaults()["diskhygiene_dpkg_cfg"])
    # dpkg ignores files in dpkg.cfg.d whose names have a dot or other chars.
    _check(re.fullmatch(r"[A-Za-z0-9_-]+", name) is not None, "drop-in name %r has no dot (dpkg reads it)" % name)
    _check(os.path.dirname(_defaults()["diskhygiene_dpkg_cfg"]) == "/etc/dpkg/dpkg.cfg.d", "drop-in in /etc/dpkg/dpkg.cfg.d")


def test_template_renders_rules_in_dpkg_order():
    conf = _render(_defaults())
    lines = [ln for ln in conf.splitlines() if ln and not ln.startswith("#")]
    _check("{{" not in conf and "{%" not in conf, "template renders fully")
    kinds = [ln.split("=", 1)[0] for ln in lines]
    _check(kinds and set(kinds) <= {"path-exclude", "path-include"}, "only path-exclude/path-include lines")
    _check(kinds == sorted(kinds, key=lambda k: k != "path-exclude"), "every include comes after the excludes")
    for must in ("path-include=/usr/share/doc/*/copyright", "path-include=/usr/share/locale/en*"):
        _check(must in lines, "keeps %s" % must)


def test_default_rules_are_valid():
    probs = dh.check_rules(_rules(_defaults()))
    _check(not probs, "default rules stay inside /usr/share/<dir> %s" % (probs or ""))


def test_default_groups_name_no_protected_package():
    d = _defaults()
    for name, pkgs in d["diskhygiene_purge_groups"].items():
        hits = [p for p in pkgs if any(fnmatch.fnmatchcase(p, g) for g in d["diskhygiene_protect"])]
        _check(not hits, "group %s names no protected package %s" % (name, hits or ""))
        _check(all(dh.PKG_RE.match(p) for p in pkgs), "group %s has valid package names" % name)


def test_protect_covers_the_stack():
    prot = _defaults()["diskhygiene_protect"]
    for pkg in STACK:
        _check(any(fnmatch.fnmatchcase(pkg, g) for g in prot), "protect list covers %s" % pkg)


def test_only_the_owners_approved_groups():
    # The maintainer 2026-11-15 approved `toolchain` only ("we build from the MacBook"); every other group stays his call.
    # #129: the role reads the lever hk_purge_groups; its declared default is the approved list.
    _check(_defaults()["diskhygiene_purge"] == "{{ hk_purge_groups }}", "diskhygiene_purge is the lever hk_purge_groups")
    cfg = _read("config", "variables.d", "storage.yml", base=os.path.join(ROLE, "..", "..", ".."))
    _check(re.search(r"(?m)^  hk_purge_groups:\n(?:    .*\n)*?    default: \[toolchain\]\s*$", cfg) is not None,
           "only the toolchain group is approved by default (the maintainer's call)")


def test_tasks_rules():
    probs = rule_tasks(_read("tasks", "main.yml"))
    _check(not probs, "writes gated, reads sudo-free, no autoremove, planned purge %s" % (probs or ""))


def test_site_wires_role_in_pios_play():
    site = _read("site.yml", base=ANSIBLE)
    _check(re.search(r"role: diskhygiene,\s*tags: \[diskhygiene\]", site) is not None,
           "site.yml wires diskhygiene with tag diskhygiene")
    _check(site.find("role: diskhygiene") > site.find("hosts: pios") > 0, "diskhygiene is in the pios play")


# ---- prune on a fake tree ---------------------------------------------------------------------
def _tree():
    root = tempfile.mkdtemp(prefix="dh-test-")
    files = {
        "usr/share/doc/foo/README.gz": b"x" * 5000,
        "usr/share/doc/foo/copyright": b"c",
        "usr/share/doc/bar/changelog.Debian.gz": b"y" * 3000,
        "usr/share/man/man1/ls.1.gz": b"m" * 2000,
        "usr/share/locale/de/LC_MESSAGES/x.mo": b"d" * 1000,
        "usr/share/locale/en_GB/LC_MESSAGES/x.mo": b"e",
        "usr/share/locale/locale.alias": b"a",
        "usr/share/fonts/keep.ttf": b"f",
        "usr/lib/keep.so": b"l",
        "outside/target/file": b"t",
    }
    for rel, data in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    # a package's doc entry that is a symlink to a sibling's doc dir (Debian: cpp-14 -> its base package): it stays,
    # or the package loses its only reachable copyright file (#293)
    os.symlink(os.path.join(root, "outside", "target"), os.path.join(root, "usr/share/doc/baz"))
    return root


def _exists(root, rel):
    return os.path.lexists(os.path.join(root, rel))


def test_prune_measures_then_deletes_only_excluded():
    root = _tree()
    try:
        rules = _rules(_defaults())
        m = dh.prune(rules, root=root, apply=False)
        _check(m["files"] == 4 and m["bytes"] > 0, "dry prune counts 4 excluded files (got %d)" % m["files"])
        _check(_exists(root, "usr/share/doc/foo/README.gz"), "dry prune deletes nothing")
        a = dh.prune(rules, root=root, apply=True)
        _check(a["files"] == 4, "apply removes the same 4 files")
        for gone in ("usr/share/doc/foo/README.gz", "usr/share/doc/bar", "usr/share/man/man1",
                     "usr/share/locale/de"):
            _check(not _exists(root, gone), "removed %s" % gone)
        for kept in ("usr/share/doc/foo/copyright", "usr/share/locale/en_GB/LC_MESSAGES/x.mo",
                     "usr/share/locale/locale.alias", "usr/share/fonts/keep.ttf", "usr/lib/keep.so",
                     "outside/target/file", "usr/share/doc", "usr/share/man", "usr/share/doc/baz"):
            _check(_exists(root, kept), "kept %s" % kept)
        again = dh.prune(rules, root=root, apply=False)
        _check(again["files"] == 0, "second run finds nothing (idempotent)")
    finally:
        shutil.rmtree(root)


def test_cli_prune_json():
    root = _tree()
    try:
        import io, contextlib, json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dh.main(["prune", "--exclude", "/usr/share/doc/*/*", "--include", "/usr/share/doc/*/copyright",
                          "--root", root])
        out = json.loads(buf.getvalue())
        _check(rc == 0 and out["files"] == 2, "cli prune: rc 0, 2 doc files, the doc symlink stays (got %s)" % out)
    finally:
        shutil.rmtree(root)


# ---- purge plan (apt-get -s faked) ------------------------------------------------------------
INSTALLED = {"gcc": 100, "g++": 200, "retroarch": 23000, "retroarch-assets": 113000,
             "fonts-noto-extra": 326000, "firmware-atheros": 117000, "cage": 50,
             "libwayland-client0": 80, "mesa-vulkan-drivers": 150000, "python3": 90,
             "openssh-server": 2000, "sudo": 3000, "systemd": 13000}
CASCADES = {"fonts-noto-extra": ["retroarch", "retroarch-assets"]}


def _fake_sim(pkgs):
    lines = []
    for p in pkgs:
        lines.append("Purg %s [1.0]" % p)
        lines.extend("Purg %s:arm64 [1.0]" % c for c in CASCADES.get(p, []))
    return "\n".join(lines)


def _plan(groups, enable):
    return dh.plan(groups, enable, _defaults()["diskhygiene_protect"], INSTALLED, _fake_sim)


def test_parse_sim():
    got = dh.parse_sim("Inst x\nPurg gcc [4:14.2.0-1]\nRemv libfoo:arm64 [1.0]\nConf y\n")
    _check(got == ["gcc", "libfoo"], "parse_sim reads Purg/Remv, drops :arch (got %s)" % got)


def test_plan_clean_group():
    out, rc = _plan({"tc": ["gcc", "g++", "not-installed"]}, ["tc"])
    _check(rc == 0 and out["purge"] == ["gcc", "g++"], "clean approved group -> purge its installed pkgs")
    _check(out["freed_mb"] == round(300 / 1024, 1), "freed MB from Installed-Size")
    out, rc = _plan({"tc": ["gcc"]}, [])
    _check(rc == 0 and out["purge"] == [], "unapproved group: planned, nothing purged")


def test_real_default_groups_plan_clean():
    d = _defaults()
    out, rc = dh.plan(d["diskhygiene_purge_groups"], list(d["diskhygiene_purge_groups"]),
                      d["diskhygiene_protect"], {}, _fake_sim)
    _check(rc == 0, "every default group can be approved without tripping the guard %s" % out["errors"])


# ---- broken cases -----------------------------------------------------------------------------
def test_broken_removal_list_names_stack_package():
    for pkg in ["retroarch", "cage", "libwayland-client0", "mesa-vulkan-drivers", "python3",
                "openssh-server", "sudo", "systemd"]:
        for enable in (["bad"], []):
            out, rc = _plan({"bad": ["gcc", pkg]}, enable)
            _check(rc == 2 and out["purge"] == [] and any(pkg in e for e in out["errors"]),
                   "broken: list with %s fails (approved=%s)" % (pkg, bool(enable)))


def test_broken_cascade_into_stack():
    out, rc = _plan({"fonts": ["fonts-noto-extra"]}, ["fonts"])
    _check(rc == 2 and out["purge"] == [], "broken: approved fonts-noto-extra (cascades to retroarch) fails")
    out, rc = _plan({"fonts": ["fonts-noto-extra"]}, [])
    g = out["groups"][0]
    _check(rc == 0 and g["cascade"] == ["retroarch", "retroarch-assets"],
           "unapproved cascading group is reported, not fatal")


def test_broken_cascade_not_protected():
    global CASCADES
    saved = CASCADES
    CASCADES = {"gcc": ["libsurprise"]}
    try:
        out, rc = _plan({"tc": ["gcc"]}, ["tc"])
        _check(rc == 2 and "libsurprise" in " ".join(out["errors"]), "broken: approved group with any cascade fails")
    finally:
        CASCADES = saved


def test_broken_unknown_group_and_bad_name():
    out, rc = _plan({"tc": ["gcc"]}, ["tcc"])
    _check(rc == 2, "broken: approving an undefined group fails")
    out, rc = _plan({"tc": ["gcc", "-y"]}, [])
    _check(rc == 2, "broken: an option-like package name fails")


def test_broken_rules_outside_usr_share():
    for bad in ("/usr/share/*", "/usr/lib/*", "/*", "/etc/*", "/usr/share/../lib/*", "usr/share/doc/*"):
        probs = dh.check_rules([("exclude", bad)])
        _check(bool(probs), "broken: exclude %s is refused" % bad)
    _check(bool(dh.check_rules([("include", "/usr/share/doc/*")])), "broken: no exclude rule is refused")


def test_broken_tasks():
    t = _read("tasks", "main.yml")
    ungated = t.replace("    - not ansible_check_mode\n    - diskhygiene_plan.purge | length > 0",
                        "    - diskhygiene_plan.purge | length > 0")
    _check(any("not gated" in x for x in rule_tasks(ungated)), "broken: purge without check-mode gate is flagged")
    _check(any("autoremove" in x for x in rule_tasks(t + "\n- name: x\n  ansible.builtin.command: apt-get -y autoremove\n")),
           "broken: apt autoremove is flagged")
    sudo_read = t.replace("    executable: python3\n  check_mode: false\n  register: diskhygiene_plan_run",
                          "    executable: python3\n  become: true\n  check_mode: false\n  register: diskhygiene_plan_run")
    _check(bool(rule_tasks(sudo_read)), "broken: sudo on the read-only plan is flagged")
    _check(any("apt module" in x for x in rule_tasks(t + "\n- name: y\n  ansible.builtin.apt:\n    name: gcc\n")),
           "broken: purge via the apt module is flagged")


def test_doc_symlink_keeps_copyright_reachable():
    """#293: the old doc/* rule dropped a package's doc symlink (BROKEN); doc/*/* keeps it, so copyright resolves."""
    root = tempfile.mkdtemp(prefix="dh-test-")
    try:
        os.makedirs(os.path.join(root, "usr/share/doc/gcc-14-base"))
        for f, d in (("copyright", b"c"), ("README", b"r" * 100)):
            with open(os.path.join(root, "usr/share/doc/gcc-14-base", f), "wb") as fh:
                fh.write(d)
        os.symlink("gcc-14-base", os.path.join(root, "usr/share/doc/libstdc++6"))
        old = [("exclude", "/usr/share/doc/*"), ("include", "/usr/share/doc/*/copyright")]
        _check(dh.is_excluded("/usr/share/doc/libstdc++6", old), "BROKEN: the old doc/* rule drops the doc symlink")
        dh.prune(_rules(_defaults()), root=root, apply=True)
        _check(os.path.isfile(os.path.join(root, "usr/share/doc/libstdc++6/copyright")),
               "the package's copyright resolves through its kept doc symlink")
        _check(not _exists(root, "usr/share/doc/gcc-14-base/README"), "the README under it is pruned")
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    main(globals())
