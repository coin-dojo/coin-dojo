#!/usr/bin/env python3
"""Structure + secrets lint for the coin-dojo arcade-pack Ansible layer.

Pure stdlib so it runs with no pip installs (ansible/pytest/PyYAML not required on the laptop).
Also pytest-compatible: `pytest` discovers the test_* functions; or run directly:

    python3 ansible/tests/test_ansible_structure.py

Checks:
  1. Required files + every role's tasks/main.yml exist.
  2. site.yml wires all roles.
  3. No secrets committed (keys, hardcoded passwords/tokens, password in inventory).
  4. No ROM/BIOS binaries are committed under ansible/.
  5. YAML files use no hard tabs for indentation (invalid YAML).
  6. The secret scanner actually catches a planted secret (the deliberately-broken control).

The deliberately-BROKEN case: see tests/README.md — add `ansible_ssh_pass: hunter2` to
inventory/hosts.yml (or commit a *.pem) and this test MUST go red.
"""
import os
import re
import sys

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROLES = ["dreamcast", "cabinet_tweaks", "power", "sshd_keyonly", "journald",
         "retroarch122", "diskhygiene", "esde_common", "esde_theme", "esde_collections", "esde_verify", "session"]
REQUIRED = [
    "ansible.cfg", "site.yml", "requirements.yml", ".gitignore",
    "inventory/hosts.yml", "group_vars/all.yml", "bin/deploy",
    "README.md",
]

# Obvious committed-secret signatures.
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key material"),
    (re.compile(r"(?mi)^\s*ansible_ssh_pass\s*:\s*[\"']?\S"), "ansible_ssh_pass with a value"),
    (re.compile(r"(?mi)^\s*ansible_password\s*:\s*[\"']?\S"), "ansible_password with a value"),
]
BINARY_EXT = (".zip", ".7z", ".chd", ".iso", ".bin", ".img", ".cue", ".gba",
              ".nes", ".sfc", ".smc", ".z64", ".n64", ".gdi", ".cdi")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check as _check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _walk_files():
    for root, dirs, files in os.walk(ANSIBLE):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".ansible")]
        for f in files:
            yield os.path.join(root, f)


def test_required_files_exist():
    for rel in REQUIRED:
        _check(os.path.isfile(os.path.join(ANSIBLE, rel)), "exists: %s" % rel)


def test_roles_have_tasks():
    for role in ROLES:
        p = os.path.join(ANSIBLE, "roles", role, "tasks", "main.yml")
        _check(os.path.isfile(p), "role %s has tasks/main.yml" % role)


def test_site_wires_all_roles():
    site = _read(os.path.join(ANSIBLE, "site.yml"))
    for role in ROLES:
        _check(re.search(r"role:\s*%s\b" % re.escape(role), site) is not None,
               "site.yml wires role: %s" % role)


def test_no_committed_secrets():
    for path in _walk_files():
        if path.endswith(".pyc"):
            continue
        rel = os.path.relpath(path, ANSIBLE)
        # Our own regex library of patterns is allowed to contain the pattern text.
        if rel.startswith("tests" + os.sep):
            continue
        try:
            text = _read(path)
        except OSError:
            continue
        for rx, label in SECRET_PATTERNS:
            _check(rx.search(text) is None, "no %s in %s" % (label, rel))


def test_inventory_has_no_password():
    inv = _read(os.path.join(ANSIBLE, "inventory", "hosts.yml"))
    _check(re.search(r"(?mi)^\s*ansible_(ssh_)?pass(word)?\s*:\s*\S", inv) is None,
           "inventory/hosts.yml carries no password")


def test_no_game_binaries():
    # No game binaries should be tracked anywhere under the ansible dir.
    for path in _walk_files():
        if path.lower().endswith(BINARY_EXT):
            rel = os.path.relpath(path, ANSIBLE)
            _check(False, "no committed game/image binary: %s" % rel)


def test_yaml_has_no_hard_tabs():
    for path in _walk_files():
        if not path.endswith((".yml", ".yaml")):
            continue
        rel = os.path.relpath(path, ANSIBLE)
        lines = _read(path).splitlines()
        bad = [i + 1 for i, ln in enumerate(lines) if "\t" in ln[:len(ln) - len(ln.lstrip())]]
        _check(not bad, "no leading tabs in %s %s" % (rel, bad or ""))


def test_secret_scanner_catches_planted_secret():
    """Deliberately-broken control: the scanner MUST flag a planted secret."""
    planted = 'ansible_ssh_pass: "hunter2"'
    hit = any(rx.search(planted) for rx, _ in SECRET_PATTERNS)
    _check(hit, "secret scanner catches a planted ansible_ssh_pass (broken-case control)")


if __name__ == "__main__":
    main(globals())
