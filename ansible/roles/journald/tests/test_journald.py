#!/usr/bin/env python3
"""Tests for the `journald` role: persistent, size-capped systemd journal on the Debian cabinet.

Pure stdlib (no ansible/PyYAML), pytest-compatible, or run directly:
    python3 ansible/roles/journald/tests/test_journald.py

Each rule works on file TEXT, so it also runs against deliberately broken copies (test_broken_*):
those MUST be flagged, proving the lint can fail.
"""
import os
import re
import sys

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def _read(*parts, base=ROLE):
    with open(os.path.join(base, *parts), encoding="utf-8") as fh:
        return fh.read()


def _lever(name):
    """Default of a lever declared in config/variables.d/storage.yml (#129), read as text."""
    text = _read("config", "variables.d", "storage.yml", base=os.path.join(ANSIBLE, ".."))
    m = re.search(r"(?m)^  %s:\n(?:    .*\n)*?    default: (.+?)\s*$" % re.escape(name), text)
    return m.group(1).strip('"') if m else None


def _defaults():
    """Flat `key: value` pairs from defaults/main.yml; a `{{ hk_* }}` value is replaced by the lever's default."""
    out = {}
    for ln in _read("defaults", "main.yml").splitlines():
        m = re.match(r"^(\w+):\s*(.+?)\s*$", ln)
        if m:
            v = re.sub(r"\s+#.*$", "", m.group(2)).strip('"')
            out[m.group(1)] = re.sub(r"\{\{\s*(hk_\w+)\s*\}\}", lambda x: _lever(x.group(1)) or x.group(0), v)
    return out


def _render(template, defaults):
    """Minimal Jinja: substitute {{ var }} from defaults (the template uses nothing else)."""
    def sub(m):
        return defaults.get(m.group(1), "{{ %s }}" % m.group(1))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, template)


def _size(s):
    m = re.fullmatch(r"(\d+)([KMGT]?)", s.strip())
    if not m:
        return None
    return int(m.group(1)) * {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}[m.group(2)]


# ---- rules over text: each returns a list of problems (empty = ok) ----------------------------
def rule_conf(conf):
    """The rendered drop-in: persistent, size-capped, free-space floor, retention."""
    p = []
    lines = [ln.strip() for ln in conf.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines or lines[0] != "[Journal]":
        p.append("drop-in does not start with [Journal]")
    kv = {}
    for ln in lines[1:]:
        if "=" in ln:
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip()
    if kv.get("Storage") != "persistent":
        p.append("Storage is %r, not persistent" % kv.get("Storage"))
    cap = _size(kv.get("SystemMaxUse", ""))
    if cap is None:
        p.append("no SystemMaxUse cap (journal could fill the SD card)")
    elif cap > 200 << 20:
        p.append("SystemMaxUse %s is too big for the SD card (max 200M)" % kv["SystemMaxUse"])
    if _size(kv.get("SystemKeepFree", "")) is None:
        p.append("no SystemKeepFree floor")
    if not re.fullmatch(r"\d+\s*(s|min|h|day|week|month|year)s?", kv.get("MaxRetentionSec", "")):
        p.append("no MaxRetentionSec")
    return p


def rule_keep_free(defaults, root_free_bytes):
    """SystemKeepFree must be below the cabinet's free space, else journald keeps ~nothing."""
    kf = _size(defaults.get("journald_system_keep_free", ""))
    cap = _size(defaults.get("journald_system_max_use", "")) or 0
    if kf is None:
        return ["journald_system_keep_free not set"]
    if kf + cap > root_free_bytes:
        return ["SystemKeepFree %s + cap leaves no room on a / with %dM free"
                % (defaults["journald_system_keep_free"], root_free_bytes >> 20)]
    return []


def rule_dropin_name(defaults):
    """Must sort after Pi OS's 40-rpi-volatile-storage.conf and end in .conf."""
    name = os.path.basename(defaults.get("journald_dropin", ""))
    p = []
    if not name.endswith(".conf"):
        p.append("drop-in %r does not end in .conf (journald ignores it)" % name)
    if not name > "40-rpi-volatile-storage.conf":
        p.append("drop-in %r sorts before 40-rpi-volatile-storage.conf (volatile wins)" % name)
    if defaults.get("journald_dropin_dir") != "/etc/systemd/journald.conf.d":
        p.append("drop-in dir is not /etc/systemd/journald.conf.d")
    return p


def _strip_comments(text):
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def rule_tasks_handlers(tasks, handlers):
    tasks, handlers = _strip_comments(tasks), _strip_comments(handlers)
    p = []
    if "notify: restart journald" not in tasks:
        p.append("drop-in change does not notify the restart handler")
    if not re.search(r"(?m)^- name: restart journald$", handlers):
        p.append("no 'restart journald' handler")
    elif not re.search(r"name: systemd-journald\s+state: restarted", handlers):
        p.append("restart handler does not restart systemd-journald")
    if "journalctl --flush" not in handlers:
        p.append("no journalctl --flush after the restart")
    # restart only via the handler, never unconditionally in tasks
    if re.search(r"state:\s*restarted|systemctl\s+restart", tasks):
        p.append("journald restarted in tasks (must be a handler: only on change)")
    if "systemd-tmpfiles --create --prefix {{ journald_dir }}" not in tasks:
        p.append("no systemd-tmpfiles --create --prefix for the journal dir")
    if not re.search(r"group: systemd-journal\s+mode: \"2755\"", tasks):
        p.append("journal dir not root:systemd-journal 2755")
    if re.search(r"(?m)^\s*-?\s*(name: )?.*\b(reboot|poweroff)\b", tasks + handlers):
        p.append("role reboots or powers off")
    return p


# ---- tests on the real files ------------------------------------------------------------------
def test_conf_rendered():
    d = _defaults()
    conf = _render(_read("templates", "50-coin-dojo.conf.j2"), d)
    _check("{{" not in conf, "template renders with only defaults")
    probs = rule_conf(conf)
    _check(not probs, "drop-in: persistent, capped, floor, retention %s" % (probs or ""))


def test_dropin_name_and_dir():
    probs = rule_dropin_name(_defaults())
    _check(not probs, "drop-in path wins over 40-rpi-volatile-storage.conf %s" % (probs or ""))


def test_keep_free_fits_the_cabinet():
    # / on the cabinet had 810M free on 2026-11-14 (df -h /).
    probs = rule_keep_free(_defaults(), 810 << 20)
    _check(not probs, "SystemKeepFree + cap fit the cabinet's free space %s" % (probs or ""))


def test_tasks_and_handlers():
    probs = rule_tasks_handlers(_read("tasks", "main.yml"), _read("handlers", "main.yml"))
    _check(not probs, "restart only on change, flush, tmpfiles, dir perms %s" % (probs or ""))


def test_site_wires_role_in_pios_play():
    site = _read("site.yml", base=ANSIBLE)
    pios = site[site.find("hosts: pios"):]
    _check(re.search(r"role: journald,\s*tags: \[journald\]", pios) is not None,
           "site.yml wires journald with tag journald in the pios play")
    _check(site.find("role: journald") > site.find("hosts: pios") > 0,
           "journald is not in the Batocera play")


# ---- broken cases: each rule MUST flag a deliberately broken copy -----------------------------
GOOD_CONF = ("[Journal]\nStorage=persistent\nSystemMaxUse=50M\nSystemKeepFree=500M\n"
             "MaxRetentionSec=1month\n")


def test_broken_storage_volatile_or_auto():
    for bad in ("volatile", "auto", "none"):
        conf = GOOD_CONF.replace("Storage=persistent", "Storage=" + bad)
        _check(any("Storage" in x for x in rule_conf(conf)), "broken: Storage=%s is flagged" % bad)
    conf = GOOD_CONF.replace("Storage=persistent\n", "")
    _check(any("Storage" in x for x in rule_conf(conf)), "broken: no Storage= line is flagged")


def test_broken_no_size_cap():
    conf = GOOD_CONF.replace("SystemMaxUse=50M\n", "")
    _check(any("SystemMaxUse" in x for x in rule_conf(conf)), "broken: no SystemMaxUse cap is flagged")
    conf = GOOD_CONF.replace("SystemMaxUse=50M", "SystemMaxUse=4G")
    _check(any("too big" in x for x in rule_conf(conf)), "broken: SystemMaxUse=4G is flagged")
    conf = GOOD_CONF.replace("SystemKeepFree=500M\n", "")
    _check(any("SystemKeepFree" in x for x in rule_conf(conf)), "broken: no SystemKeepFree is flagged")


def test_broken_keep_free_too_big():
    d = dict(_defaults(), journald_system_keep_free="1G")
    _check(bool(rule_keep_free(d, 810 << 20)), "broken: SystemKeepFree=1G on 810M free is flagged")


def test_broken_dropin_sorts_before_rpi():
    d = dict(_defaults(), journald_dropin="/etc/systemd/journald.conf.d/10-coin-dojo.conf")
    _check(bool(rule_dropin_name(d)), "broken: 10- drop-in (volatile would win) is flagged")
    d = dict(_defaults(), journald_dropin="/etc/systemd/journald.conf.d/50-coin-dojo.cfg")
    _check(bool(rule_dropin_name(d)), "broken: non-.conf drop-in is flagged")


def test_broken_no_restart_handler():
    tasks, handlers = _read("tasks", "main.yml"), _read("handlers", "main.yml")
    no_handler = re.sub(r"(?s)- name: restart journald.*?(?=\n- name:|\Z)", "", handlers)
    _check(any("handler" in x for x in rule_tasks_handlers(tasks, no_handler)),
           "broken: missing restart handler is flagged")
    no_notify = tasks.replace("notify: restart journald", "")
    _check(any("notify" in x for x in rule_tasks_handlers(no_notify, handlers)),
           "broken: drop-in without notify is flagged")
    always = tasks + "\n- name: x\n  ansible.builtin.systemd:\n    name: systemd-journald\n    state: restarted\n"
    _check(any("only on change" in x for x in rule_tasks_handlers(always, handlers)),
           "broken: unconditional restart in tasks is flagged")
    no_flush = handlers.replace("journalctl --flush", "true")
    _check(any("flush" in x for x in rule_tasks_handlers(tasks, no_flush)),
           "broken: no journalctl --flush is flagged")


def test_broken_no_tmpfiles():
    tasks = _read("tasks", "main.yml").replace("systemd-tmpfiles --create", "true")
    _check(any("tmpfiles" in x for x in rule_tasks_handlers(tasks, _read("handlers", "main.yml"))),
           "broken: no systemd-tmpfiles step is flagged")


if __name__ == "__main__":
    main(globals())
