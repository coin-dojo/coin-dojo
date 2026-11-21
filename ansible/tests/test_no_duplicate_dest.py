#!/usr/bin/env python3
"""No two roles write the same file (#122).

cabinet_tweaks and retroarch122 both wrote config/remaps/Mupen64Plus-Next/Mupen64Plus-Next.rmp with
different content and mode, so every deploy flipped it back and forth (changed on every run). One owner per
dest path. `written_paths()` reads the role defaults (whole-file copies: remaps, per-game core options,
retroarch122's `dest:` list) and `tasks/*.yml` literal `dest:` strings (templated `{{ item... }}` ones
are covered by the defaults lists); `duplicates()` is a function over {role: text} so the same rule runs on a
planted duplicate (test_broken_*): that MUST be flagged.

Pure stdlib. Run directly:  python3 ansible/tests/test_no_duplicate_dest.py
"""
import glob
import os
import re
import sys

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)

PAIR = re.compile(r'\{\s*core:\s*"([^"]+)",\s*file:\s*"([^"]+)"\s*\}')


def written_paths(defaults, tasks=""):
    """Paths (relative to the RetroArch dir, or the literal task dest) a role writes whole."""
    out = []
    for name, sub in (("cabinet_remaps", "config/remaps"), ("cabinet_core_option_files", "config")):
        m = re.search(r"(?ms)^%s:\s*\n((?:[ \t]+.*\n|[ \t]*\n|[ \t]*#.*\n)*)" % name, defaults)
        if m:
            out += ["%s/%s/%s" % (sub, c, f) for c, f in PAIR.findall(m.group(1))]
    out += re.findall(r'(?m)^\s+dest:\s*"(config/[^"]+)"\s*$', defaults)
    for lit in re.findall(r'(?m)^\s+dest:\s*"?([^"\n]+?)"?\s*$', tasks):
        if "item" not in lit:
            out.append(lit)
    return out


def duplicates(roles):
    """roles = {role: (defaults_text, tasks_text)}. -> {path: [roles]} for paths written by more than one role."""
    seen = {}
    for role, (defaults, tasks) in roles.items():
        for p in set(written_paths(defaults, tasks)):
            seen.setdefault(p, []).append(role)
    return {p: sorted(r) for p, r in seen.items() if len(r) > 1}


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _roles():
    out = {}
    for d in sorted(glob.glob(os.path.join(ANSIBLE, "roles", "*"))):
        tasks = "".join(_read(f) for f in sorted(glob.glob(os.path.join(d, "tasks", "*.yml"))))
        out[os.path.basename(d)] = (_read(os.path.join(d, "defaults", "main.yml")), tasks)
    return out


def test_no_two_roles_write_the_same_dest():
    roles = _roles()
    dup = duplicates(roles)
    check(not dup, "no two roles write the same dest path (duplicates: %s)" % (dup or "none"))
    seen = {p for d, t in roles.values() for p in written_paths(d, t)}
    check("config/remaps/Mupen64Plus-Next/Mupen64Plus-Next.rmp" in seen,
          "the scanner sees retroarch122's N64 remap")
    check("config/remaps/Genesis Plus GX/Genesis Plus GX.rmp" in seen, "the scanner sees cabinet_tweaks' remaps")


def test_n64_remap_has_one_owner_and_one_content():
    ct = _read(os.path.join(ANSIBLE, "roles", "cabinet_tweaks", "defaults", "main.yml"))
    check('file: "Mupen64Plus-Next.rmp"' not in ct, "cabinet_tweaks does not list Mupen64Plus-Next.rmp")
    a = os.path.join(ANSIBLE, "roles", "retroarch122", "files", "Mupen64Plus-Next.rmp")
    b = os.path.join(ANSIBLE, "..", "skills", "controls", "remaps", "Mupen64Plus-Next", "Mupen64Plus-Next.rmp")
    check(_read(a) == _read(b) != "", "retroarch122's .rmp is byte-identical to the skills/controls copy")
    ra = _read(os.path.join(ANSIBLE, "roles", "retroarch122", "defaults", "main.yml"))
    modes = re.findall(r'(?m)^\s+dir_mode:\s*"(\d+)"', ra)
    check(set(modes) == {"0755"}, "retroarch122 dir modes match cabinet_tweaks' remap dirs (0755): %s" % modes)


def test_broken_planted_duplicate_is_flagged():
    a = ('cabinet_remaps:\n  - { core: "Mupen64Plus-Next", file: "Mupen64Plus-Next.rmp" }\n'
         '  - { core: "Stella", file: "Stella.rmp" }\n', "")
    b = ('x:\n  - src: a\n    dest: "config/remaps/Mupen64Plus-Next/Mupen64Plus-Next.rmp"\n', "")
    check(duplicates({"one": a, "two": b}) == {"config/remaps/Mupen64Plus-Next/Mupen64Plus-Next.rmp": ["one", "two"]},
          "BROKEN: a planted dest written by two roles is flagged")
    check(duplicates({"one": a, "two": ("", "")}) == {}, "one owner is not a duplicate")
    t1 = ("- ansible.builtin.copy:\n    dest: /etc/foo.conf\n", "")
    check(duplicates({"one": ("", t1[0]), "two": ("", t1[0])}) == {"/etc/foo.conf": ["one", "two"]},
          "BROKEN: the same literal task dest in two roles is flagged")


if __name__ == "__main__":
    main(globals())
