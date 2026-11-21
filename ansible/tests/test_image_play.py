#!/usr/bin/env python3
"""The image build runs the cabinet's own roles (#244): ansible/image.yml against the image's chroot.

    python3 ansible/tests/test_image_play.py

Checks, each a rule over file TEXT with a broken case that must FAIL:
  1. image.yml runs site.yml's cabinet roles in site.yml's order; only sshd_keyonly and esde_verify are left out (and
     fightcade sits behind hk_image_fightcade).
  2. No role task the image runs touches the BUILD machine's live system without a coin_dojo_image guard: sysctl
     --system, modprobe, binfmt registration, nmcli, timedatectl show/set, systemd start/restart/reload, journalctl.
     A guard is `not coin_dojo_image` under when:/daemon_reload: or `state: omit if coin_dojo_image`; an inverted
     `when: coin_dojo_image`, a comment or failed_when is not. coin_dojo_image is false in group_vars/all.yml and only
     image.yml sets it true.
  3. The inventory is a chroot (never SSH) and the play refuses any other host.
  4. The image's key-only sshd drop-in (chroot-setup.sh) states the same directives as the sshd_keyonly role's file.
  5. hk_image_fightcade is declared off by default and image.yml reads it.
"""
import os
import re
import sys

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ANSIBLE)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main  # noqa: E402

OUT_OF_IMAGE = {"sshd_keyonly", "esde_verify"}


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def play_roles(text, first_play_only=True):
    """Role names in the `roles:` list of the first play, in order."""
    m = re.search(r"(?ms)^  roles:\n(.*?)(?=^  [a-z_]+:|^- |\Z)", text)
    if not m:
        return []
    return re.findall(r"(?m)^    - (?:\{ ?)?role: ([a-z0-9_]+)", m.group(1))


def roles_problems(site, image):
    p = []
    want = [r for r in play_roles(site) if r not in OUT_OF_IMAGE]
    have = play_roles(image)
    if have != want:
        p.append("image.yml roles %s != site.yml cabinet roles %s (minus %s)" % (have, want, sorted(OUT_OF_IMAGE)))
    if not re.search(r"role: fightcade, tags: \[fightcade\], when: hk_image_fightcade \| bool", image):
        p.append("fightcade is not behind hk_image_fightcade")
    if not re.search(r"(?m)^    coin_dojo_image: true$", image):
        p.append("image.yml does not set coin_dojo_image: true")
    return p


# Live-system actions: (pattern, what). A task (or handler) block that matches one must name coin_dojo_image.
LIVE = [
    (r"argv: \[sysctl, --system\]", "sysctl --system"),
    (r"cmd: modprobe\b", "modprobe"),
    (r"argv: \[\"\{\{ fightcade_binfmt_cmd|printf -- '-1|argv: \[cat, \"\{\{ fightcade_binfmt_proc|magic 4d5a' \{\{",
     "binfmt_misc on the live kernel"),
    (r"nmcli general reload|nmcli -t", "nmcli"),
    (r"timedatectl, (show|set-timezone|list-timezones)", "timedatectl (needs a running systemd)"),
    (r"state: (started|restarted|reloaded)", "systemd start/restart/reload"),
    (r"systemctl (kill|--user)", "systemctl on the live manager"),
    (r"journalctl --flush", "journalctl --flush"),
    (r"'/dev/zram' in|\(session_swappiness_raw", "an assert on the live kernel's swap state"),
]


def task_blocks(text):
    """Split a tasks/handlers file into task blocks (each starts at a `- name:` line, any indent)."""
    starts = [m.start() for m in re.finditer(r"(?m)^\s*- name:", text)] + [len(text)]
    return [text[a:b] for a, b in zip(starts, starts[1:])]


def _strip_comments(block):
    """The block without YAML comments (a `#` at a line start or after a space): a guard in a comment is no guard."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in block.splitlines())


def when_terms(lines):
    """The top-level AND terms of a when: (its lines; list items are ANDed), parenthesised groups folded to GROUP.
    None when the top level has an `or` (then no single term decides)."""
    parts = []
    for l in lines:
        l = re.sub(r"^\s*when:\s*", "", l)
        l = re.sub(r"^[>|][-+]?\s*$", "", l.strip())
        parts.append(re.sub(r"^- ", " and ", l.strip()))
    expr = " ".join(parts).strip()
    expr = re.sub(r"^and\s+", "", expr)
    while re.search(r"\([^()]*\)", expr):
        expr = re.sub(r"\([^()]*\)", "GROUP", expr)
    if re.search(r"\sor\s", expr):
        return None
    return [t.strip() for t in re.split(r"\sand\s", expr)]


def has_image_guard(block):
    """True when the task itself is switched off in image mode: one top-level AND term of its when: is
    `not coin_dojo_image ...` (an `or` at the top level, as in `not coin_dojo_image or true`, defeats it), or its state:
    is `"{{ omit if coin_dojo_image ... }}"`. An inverted `when: coin_dojo_image | bool`, a comment, daemon_reload:,
    failed_when, backup or force do not count."""
    key, when = None, []
    for line in _strip_comments(block).splitlines():
        m = re.match(r"^\s*(?:- )?([a-z_]+):", line)
        if m:
            key = m.group(1)
        if key == "when":
            when.append(line)
        if key == "state" and re.search(r"\bomit if coin_dojo_image\b", line):
            return True
    terms = when_terms(when) if when else None
    return bool(terms) and any(re.match(r"not coin_dojo_image\b", t) for t in terms)


def guard_problems(name, text):
    p = []
    for b in task_blocks(text):
        for rx, what in LIVE:
            # a block nested in a guarded `block:` counts as guarded only through its own when: keep it explicit
            if re.search(rx, _strip_comments(b)) and not has_image_guard(b) and "listen: restart es-de" not in b:
                title = b.splitlines()[0].strip()
                p.append("%s: %s (%s) runs in the image build without a coin_dojo_image guard" % (name, title, what))
    return p


def switch_problems(texts):
    """coin_dojo_image is false in group_vars/all.yml and set true only by image.yml (texts: {relpath: text})."""
    p = []
    if not re.search(r"(?m)^coin_dojo_image: false$", texts.get("group_vars/all.yml", "")):
        p.append("group_vars/all.yml does not declare coin_dojo_image: false")
    for rel, t in sorted(texts.items()):
        if rel != "image.yml" and re.search(r"(?m)^[^#\n]*\bcoin_dojo_image\s*[:=]\s*(true|yes|True|1)\b", t):
            p.append("%s sets coin_dojo_image true (only image.yml may)" % rel)
    return p


def ansible_yml_texts():
    out = {}
    for dp, dns, fs in os.walk(ANSIBLE):
        dns[:] = [d for d in dns if d not in ("tests", ".ansible", "collections")]
        for f in fs:
            if f.endswith((".yml", ".yaml", ".j2", ".cfg")) or f in ("ans", "deploy"):
                rel = os.path.relpath(os.path.join(dp, f), ANSIBLE)
                with open(os.path.join(dp, f), encoding="utf-8", errors="replace") as fh:
                    out[rel] = fh.read()
    return out


def image_role_files(image):
    out = []
    for r in play_roles(image):
        for sub in ("tasks", "handlers"):
            d = os.path.join(ANSIBLE, "roles", r, sub)
            for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                if f.endswith(".yml"):
                    out.append(("roles/%s/%s/%s" % (r, sub, f), _read("ansible", "roles", r, sub, f)))
    return out


def test_image_runs_the_cabinet_roles():
    p = roles_problems(_read("ansible", "site.yml"), _read("ansible", "image.yml"))
    check(not p, "image.yml = site.yml's cabinet roles in order: %s" % p)


def test_broken_role_list_caught():
    site, image = _read("ansible", "site.yml"), _read("ansible", "image.yml")
    check(roles_problems(site, image.replace("    - { role: session, tags: [session] }\n", "")),
          "BROKEN: a role dropped from image.yml is caught")
    check(roles_problems(site, image.replace(", when: hk_image_fightcade | bool", "")),
          "BROKEN: fightcade without its switch is caught")


def test_no_live_action_unguarded():
    image = _read("ansible", "image.yml")
    files = image_role_files(image)
    check(len(files) > 15, "found the image roles' task and handler files (%d)" % len(files))
    p = [x for n, t in files for x in guard_problems(n, t)]
    check(not p, "no live-system action without a coin_dojo_image guard: %s" % p)


def test_broken_unguarded_sysctl_caught():
    h = _read("ansible", "roles", "session", "handlers", "main.yml")
    broken = re.sub(r"(?m)^  when: not coin_dojo_image.*\n", "", h)
    check(broken != h and guard_problems("session handlers", broken), "BROKEN: sysctl --system without its guard is caught")
    t = _read("ansible", "roles", "power", "tasks", "main.yml")
    broken = t.replace('"{{ omit if coin_dojo_image | default(false) | bool else \'started\' }}"', "started").replace(
        '"{{ not coin_dojo_image | default(false) | bool }}"', "true")
    check(broken != t and guard_problems("power", broken), "BROKEN: a systemd start without its guard is caught")
    f = _read("ansible", "roles", "fightcade", "tasks", "main.yml")
    broken = f.replace("not ansible_check_mode and not coin_dojo_image | default(false) | bool and (fightcade_binfmt_have",
                       "not ansible_check_mode and (fightcade_binfmt_have")
    check(broken != f and guard_problems("fightcade", broken), "BROKEN: registering binfmt on the build kernel is caught")


def test_broken_weak_guards_caught():
    """Should-fix 1 (#244 fix round): only a real switch-off counts as a guard."""
    t = _read("ansible", "roles", "journald", "handlers", "main.yml")
    inverted = t.replace("when: not coin_dojo_image", "when: coin_dojo_image")
    check(inverted != t and guard_problems("journald handlers", inverted), "BROKEN: an inverted guard (when: coin_dojo_image) is caught")
    commented = re.sub(r"(?m)^  when: not coin_dojo_image.*$", "  # when: not coin_dojo_image | default(false) | bool", t)
    check(commented != t and guard_problems("journald handlers", commented), "BROKEN: a guard in a comment is caught")
    s = _read("ansible", "roles", "session", "handlers", "main.yml")
    fw = s.replace("  when: not coin_dojo_image", "  failed_when: not coin_dojo_image")
    check(fw != s and guard_problems("session handlers", fw), "BROKEN: failed_when: not coin_dojo_image is no guard")
    for name, bad in (
            ("daemon_reload is no guard",
             '- name: R\n  ansible.builtin.systemd:\n    name: x\n    state: restarted\n    daemon_reload: "{{ not coin_dojo_image }}"\n'),
            ("`not coin_dojo_image or true` is always on",
             "- name: R\n  ansible.builtin.systemd:\n    name: x\n    state: restarted\n  when: not coin_dojo_image or true\n"),
            ("`coin_dojo_image or not coin_dojo_image` is always on",
             "- name: R\n  ansible.builtin.systemd:\n    name: x\n    state: restarted\n  when: coin_dojo_image or not coin_dojo_image\n"),
            ("an `or` across when: lines is always on",
             "- name: R\n  ansible.builtin.systemd:\n    name: x\n    state: restarted\n  when: >-\n    not coin_dojo_image\n    or true\n")):
        check(guard_problems("x", bad), "BROKEN: %s" % name)
    listed = "- name: Restart x\n  ansible.builtin.systemd:\n    name: x\n    state: restarted\n  when:\n    - not coin_dojo_image | default(false) | bool\n"
    check(not guard_problems("x", listed), "a when: list item `- not coin_dojo_image` counts")


def test_image_switch_only_in_image_yml():
    texts = ansible_yml_texts()
    check("image.yml" in texts and "group_vars/all.yml" in texts, "found ansible/image.yml and group_vars/all.yml")
    p = switch_problems(texts)
    check(not p, "coin_dojo_image: false by default, true only in image.yml: %s" % p)
    bad = dict(texts)
    bad["group_vars/all.yml"] = bad["group_vars/all.yml"].replace("coin_dojo_image: false", "coin_dojo_image: true")
    check(len(switch_problems(bad)) == 2, "BROKEN: group_vars turning image mode on is caught")
    bad = dict(texts, **{"roles/session/defaults/main.yml": texts.get("roles/session/defaults/main.yml", "") + "coin_dojo_image: true\n"})
    check(switch_problems(bad), "BROKEN: a role default turning image mode on is caught")


def test_inventory_is_a_chroot():
    inv = _read("ansible", "inventory", "image.yml")
    check("ansible_connection: community.general.chroot" in inv and "ansible_host: /mnt/root" in inv,
          "inventory/image.yml: chroot at /mnt/root")
    check(not re.search(r"(?m)^\s*ansible_(ssh|user|port|become_pass)", inv), "no SSH, user or password in the image inventory")
    image = _read("ansible", "image.yml")
    check("ansible_connection == 'community.general.chroot'" in image and "inventory_hostname == 'coin-dojo-image'" in image,
          "image.yml refuses any host that is not the image chroot")
    check("hosts: image" in image and "pios" not in re.findall(r"hosts: (\S+)", image), "image.yml targets only the image group")


def sshd_directives(text):
    return sorted(set(l.strip() for l in text.splitlines() if re.match(r"^[A-Z][A-Za-z]+ ", l.strip())))


def test_image_sshd_dropin_matches_the_role():
    role = sshd_directives(_read("ansible", "roles", "sshd_keyonly", "files", "00-coin-dojo-keyonly.conf"))
    body = _read("skills", "build-image", "scripts", "chroot-setup.sh")
    m = re.search(r"cat > /etc/ssh/sshd_config\.d/00-coin-dojo-keyonly\.conf <<SSHD\n(.*?)\nSSHD", body, re.S)
    check(m is not None, "chroot-setup.sh writes the key-only drop-in")
    image = sshd_directives(m.group(1).replace("$SSH_PW_AUTH", "no")) if m else []
    check(image == role, "image drop-in (default) == roles/sshd_keyonly file: %s vs %s" % (image, role))
    check(sshd_directives(m.group(1).replace("$SSH_PW_AUTH", "yes")) != role if m else False,
          "BROKEN: the opt-in (password yes) differs from the role file")


def sdl_problems(chroot_setup, dreamcast_defaults):
    m = re.search(r'(?m)^dreamcast_sdl_gamecontrollerconfig: "([^"]+)"', dreamcast_defaults)
    want = m.group(1) if m else None
    lines = re.findall(r"0300cb231f08000001e4000010010000,USB gamepad,[^'\n]*", chroot_setup)
    if not want or len(lines) < 2:
        return ["no SDL map found (dreamcast default %s, chroot-setup %d lines)" % (bool(want), len(lines))]
    return ["chroot-setup.sh SDL map %s != the cabinet's %s" % (l, want) for l in lines if l != want]


def test_image_pad_map_is_the_cabinets():
    """#244: the image's ES-DE / SDL pad map (.bash_profile, es_controllers.txt) is the cabinet's one."""
    cs = _read("skills", "build-image", "scripts", "chroot-setup.sh")
    dc = _read("ansible", "roles", "dreamcast", "defaults", "main.yml")
    check(not sdl_problems(cs, dc), "image SDL map == dreamcast_sdl_gamecontrollerconfig: %s" % sdl_problems(cs, dc))
    old = cs.replace("a:b3,b:b2,x:b0,y:b4,leftshoulder:b5", "a:b4,b:b0,x:b5,y:b3,leftshoulder:b2")
    check(sdl_problems(old, dc), "BROKEN: the pre-#244 image map (a:b4 ...) is caught")


def test_starter_carousel():
    """#244: the new image's carousel = SETTINGS + QUIT from nested.py, without <loadExclusive/> (real systems show)."""
    import json
    import subprocess
    r = subprocess.run([sys.executable, "-I", os.path.join(ANSIBLE, "roles", "esde_collections", "files", "starter.py"),
                        "--tools", os.path.join(REPO, "skills", "collections"), "--nested-root", "/data/nested",
                        "--settings-command", "python3 S", "--quit-command", "bash Q menu"], capture_output=True, text=True)
    check(r.returncode == 0, "starter.py runs (%s)" % r.stderr.strip()[-200:])
    d = json.loads(r.stdout or "{}")
    xml = d.get("es_systems", "")
    check("<loadExclusive/>" not in xml, "no <loadExclusive/>: the bundled systems stay visible")
    check("<name>settings</name>" in xml and "<name>quit</name>" in xml and "bash Q menu %ROM%" in xml,
          "SETTINGS and QUIT TO MAIN MENU with their commands")
    check(sorted(d.get("gamelists", {})) == ["quit", "settings"] and d.get("links") == {"quit": "Quit.hks", "settings": "Settings.hks"},
          "their gamelists and entry links")


def test_fightcade_switch_declared():
    decl = _read("config", "variables.d", "fightcade.yml")
    check(re.search(r"(?m)^  hk_image_fightcade:\n    type: bool\n    default: true$", decl) is not None,
          "hk_image_fightcade: bool, default true (#294: Fightcade mode unless left out)")
    check("hk_image_fightcade" in _read("skills", "build-image", "scripts", "run.sh"),
          "run.sh reads hk_image_fightcade (data partition size)")


if __name__ == "__main__":
    main(globals())
