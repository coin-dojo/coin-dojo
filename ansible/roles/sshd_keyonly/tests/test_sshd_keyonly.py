#!/usr/bin/env python3
"""Tests for the `sshd_keyonly` role (#41): key-only SSH on the Debian cabinet, lockout-guarded.

Pure stdlib (no ansible/PyYAML), pytest-compatible, or run directly:
    python3 ansible/roles/sshd_keyonly/tests/test_sshd_keyonly.py

Each rule works on file TEXT, so it also runs against deliberately broken copies (test_broken_*):
those MUST be flagged, proving the lint can fail. If /usr/sbin/sshd exists, the drop-in is also
checked against real OpenSSH precedence (it must beat a later `PasswordAuthentication yes`).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
sys.path.insert(0, os.path.join(ANSIBLE, "..", "tests", "lib"))
from hktest import check as _check, main, skip  # noqa: E402  (counts SKIP lines in the RESULT line, #111)


def _read(*parts, base=ROLE):
    with open(os.path.join(base, *parts), encoding="utf-8") as fh:
        return fh.read()


# ---- rules over text: each returns a list of problems (empty = ok) ----------------------------
def rule_dropin(conf):
    p = []
    for key in ("PasswordAuthentication", "KbdInteractiveAuthentication"):
        if not re.search(r"(?m)^%s no$" % key, conf):
            p.append("%s no missing" % key)
        if re.search(r"(?mi)^\s*%s\s+yes" % key, conf):
            p.append("%s yes in the drop-in" % key)
    if not re.search(r"(?m)^PubkeyAuthentication yes$", conf):
        p.append("PubkeyAuthentication yes missing")
    if re.search(r"(?mi)^\s*Match\b", conf):
        p.append("Match block in the drop-in (would scope later lines)")
    return p


def rule_tasks(tasks):
    """The four lockout guards, in order, inside a block with a rescue."""
    p = []
    write = tasks.find('dest: "{{ sshd_keyonly_dropin }}"')
    if write < 0:
        p.append("drop-in is never installed")
        write = len(tasks)
    # 1. deploying key asserted in authorized_keys BEFORE any write
    guard = tasks.find("LOCKOUT GUARD")
    if guard < 0 or "sshd_keyonly_pub_blob in (sshd_keyonly_ak.content | b64decode)" not in tasks:
        p.append("no assert that the deploying key is in authorized_keys")
    elif guard > write:
        p.append("authorized_keys assert comes after the drop-in write")
    # 2. sshd -t: validate on the file AND the full config, before the reload
    if 'validate: "{{ sshd_keyonly_sshd }} -t -f %s"' not in tasks:
        p.append("drop-in copy has no sshd -t validate")
    reload_at = tasks.find("state: reloaded")
    full_t = [m.start() for m in re.finditer(r'command: "\{\{ sshd_keyonly_sshd \}\} -t"', tasks)]
    if not any(write < i < reload_at for i in full_t):
        p.append("no full-config sshd -t between the write and the reload")
    # 3. reload, never restart
    if reload_at < 0:
        p.append("sshd is never reloaded")
    if re.search(r"state:\s*restarted|systemctl\s+restart|service\s+ssh\s+restart", tasks):
        p.append("sshd restart (kills sessions) - must be reload")
    # 4. post-check: a NEW key connection, inside the block, after the reload
    rescue_at = tasks.find("rescue:")
    post = tasks.find("PreferredAuthentications=publickey")
    if post < 0 or "ControlPath=none" not in tasks or "IdentitiesOnly=yes" not in tasks:
        p.append("no fresh key-only ssh post-check (ControlPath=none, IdentitiesOnly)")
    elif not (reload_at < post < rescue_at):
        p.append("post-check is not between the reload and the rescue")
    if "delegate_to: localhost" not in tasks:
        p.append("post-check not run from the control node")
    # rollback
    if rescue_at < 0 or "block:" not in tasks:
        p.append("no block/rescue rollback")
    else:
        rescue = tasks[rescue_at:]
        if "sshd_keyonly_backup_dir" not in rescue:
            p.append("rescue does not restore the backup")
        if "state: reloaded" not in rescue or "-t\"" not in rescue:
            p.append("rescue does not re-validate + reload")
        if "ansible.builtin.fail" not in rescue:
            p.append("rescue does not fail the run")
    backup_at = tasks.find("Back up the drop-ins")
    if backup_at < 0 or backup_at > write:
        p.append("no backup before the write")
    # effective-config check catches other drop-ins / Match blocks
    if "-T -C" not in tasks or "'passwordauthentication no' in" not in tasks \
            or "'kbdinteractiveauthentication no' in" not in tasks:
        p.append("no effective-config (sshd -T) check for password/kbd-interactive")
    if "Authentications that can continue: publickey" not in tasks:
        p.append("no final check that the server refuses password methods")
    return p


def rule_owner(tasks, defaults):
    """#299: the owner's password-SSH drop-in relaxes ONLY the password checks, ONLY while it exists; it is never
    removed or rewritten by the role, and kbd-interactive stays checked."""
    p = []
    if not re.search(r'(?m)^sshd_keyonly_owner_dropin: "\{\{ sshd_keyonly_dropin_dir \}\}/10-coindojo-owner-ssh\.conf"', defaults):
        p.append("owner drop-in path is not 10-coindojo-owner-ssh.conf in sshd_config.d")
    if "path: \"{{ sshd_keyonly_owner_dropin }}\"" not in tasks or "register: sshd_keyonly_owner" not in tasks:
        p.append("the owner drop-in is not looked up with stat")
    relax = [l.strip() for l in tasks.splitlines() if "sshd_keyonly_owner.stat.exists" in l]
    want = ["- \"'passwordauthentication no' in sshd_keyonly_effective.stdout_lines or sshd_keyonly_owner.stat.exists\"",
            "- sshd_keyonly_owner.stat.exists or sshd_keyonly_methods.stderr is not search('Authentications that can continue:.*password')"]
    if relax != want:
        p.append("the owner drop-in relaxes something other than the two password checks: %s" % relax)
    if re.search(r"owner_dropin[^\n]*\n[^\n]*state: absent", tasks) or "dest: \"{{ sshd_keyonly_owner_dropin }}\"" in tasks:
        p.append("the role removes or writes the owner drop-in")
    if "- \"'kbdinteractiveauthentication no' in sshd_keyonly_effective.stdout_lines\"" not in tasks:
        p.append("kbd-interactive is no longer checked strictly")
    return p


def rule_idempotent_backup(tasks):
    """#122: the backup dir + copy ran (changed) on every deploy. Both must wait for will_change, the
    decision compares the live drop-in checksum with the shipped file and the legacy drop-in's presence."""
    p = []
    for name in ("- name: Backup dir", "- name: Back up the drop-ins we will replace or remove"):
        i = tasks.find(name)
        j = tasks.find("\n- name:", i + 1) if i >= 0 else -1
        block = tasks[i:j if j > 0 else len(tasks)]
        if i < 0 or "sshd_keyonly_will_change | bool" not in block:
            p.append("%r is not gated on sshd_keyonly_will_change" % name)
    m = re.search(r"sshd_keyonly_will_change: >-\n(.*?)\n\n", tasks, re.S)
    # lookup('file') strips the trailing newline unless rstrip=False: the sha1 would never match (found on the VM)
    if not m or "checksum" not in m.group(1) or "hash('sha1')" not in m.group(1) or "rstrip=False" not in m.group(1) or "results[1].stat.exists" not in m.group(1):
        p.append("will_change does not compare the drop-in checksum and the legacy drop-in")
    if "ROLLBACK - restore backed-up drop-ins" in tasks:
        r = tasks[tasks.find("ROLLBACK - restore backed-up drop-ins"):tasks.find("ROLLBACK - remove our")]
        if "sshd_keyonly_will_change | bool" not in r:
            p.append("rollback restores from a backup that may not exist")
    return p


def rule_defaults(defaults):
    p = []
    m = re.search(r"(?m)^sshd_keyonly_backup_dir:\s*\"?([^\"\n]+)", defaults)
    if not m:
        p.append("sshd_keyonly_backup_dir missing")
    elif m.group(1).startswith("/etc/ssh/sshd_config.d"):
        p.append("backups inside sshd_config.d would be Included")
    if not re.search(r"(?m)^sshd_keyonly_dropin:.*/00-coin-dojo-keyonly\.conf", defaults):
        p.append("drop-in must sort first (00-coin-dojo-keyonly.conf)")
    if not re.search(r"(?m)^sshd_keyonly_legacy:.*99-arcade\.conf", defaults):
        p.append("legacy 99-arcade.conf not handled")
    return p


def rule_site(site):
    p = []
    pios = site.find("hosts: pios")
    role = site.find("role: sshd_keyonly")
    if pios < 0 or role < pios:
        p.append("sshd_keyonly not wired into the pios play")
    return p


# ---- tests on the real files --------------------------------------------------------------------
def test_dropin():
    for prob in rule_dropin(_read("files", "00-coin-dojo-keyonly.conf")) or ["ok"]:
        _check(prob == "ok", "drop-in: " + prob)


def test_tasks():
    for prob in rule_tasks(_read("tasks", "main.yml")) or ["ok"]:
        _check(prob == "ok", "tasks lockout guards: " + prob)


def test_idempotent_backup():
    for prob in rule_idempotent_backup(_read("tasks", "main.yml")) or ["ok"]:
        _check(prob == "ok", "backup only when something changes: " + prob)


def test_defaults():
    for prob in rule_defaults(_read("defaults", "main.yml")) or ["ok"]:
        _check(prob == "ok", "defaults: " + prob)


def test_site():
    for prob in rule_site(_read("site.yml", base=ANSIBLE)) or ["ok"]:
        _check(prob == "ok", "site.yml: " + prob)


def test_no_tabs_no_secrets():
    for rel in ("tasks/main.yml", "defaults/main.yml"):
        body = _read(*rel.split("/"))
        _check("\t" not in body, "%s has no hard tabs" % rel)
        _check(not re.search(r"(?i)(become_pass|ansible_ssh_pass|ansible_password)\s*:", body),
               "%s holds no password var" % rel)
        _check("PRIVATE KEY-----" not in body, "%s holds no key material" % rel)


def test_matches_builder():
    """The cabinet and the image builder ship the same key-only drop-in."""
    repo = os.path.dirname(ANSIBLE)
    builder = _read("skills", "build-image", "scripts", "chroot-setup.sh", base=repo)
    m = re.search(r"00-coin-dojo-keyonly\.conf <<SSHD\n(.*?)\nSSHD\n", builder, re.S)
    if not _check(m is not None, "builder writes 00-coin-dojo-keyonly.conf via heredoc"):
        return
    built = m.group(1).replace("$SSH_PW_AUTH", "no") + "\n"
    _check(built == _read("files", "00-coin-dojo-keyonly.conf"),
           "builder default drop-in == role drop-in (byte for byte)")


def test_owner_dropin():
    problems = rule_owner(_read("tasks", "main.yml"), _read("defaults", "main.yml"))
    _check(not problems, "#299 owner drop-in: only the password checks relax, only while it exists (%s)" % problems)


def test_real_sshd_owner_match():
    """#299 with real OpenSSH: the owner drop-in (Match User coindojo) turns password SSH on for coindojo only, root
    login stays off, keys stay on, and other users stay key-only, behind the role's 00- file."""
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if not os.path.exists(sshd) or not shutil.which("ssh-keygen"):
        skip("real sshd owner Match (no sshd here)")
        return
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "d")
        os.mkdir(d)
        shutil.copy(os.path.join(ROLE, "files", "00-coin-dojo-keyonly.conf"), d)
        with open(os.path.join(d, "10-coindojo-owner-ssh.conf"), "w") as fh:
            fh.write("# coindojo (#299)\nMatch User coindojo\n    PasswordAuthentication yes\n")
        with open(os.path.join(t, "sshd_config"), "w") as fh:   # Debian's main file: global keywords after the Include
            fh.write("Include %s/*.conf\nKbdInteractiveAuthentication no\nUsePAM no\nX11Forwarding yes\nPrintMotd no\n"
                     "AcceptEnv LANG LC_*\nSubsystem sftp internal-sftp\n" % d)
        key = os.path.join(t, "hk")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        r = subprocess.run([sshd, "-t", "-f", os.path.join(t, "sshd_config"), "-h", key], capture_output=True, text=True)
        _check(r.returncode == 0, "real sshd -t: the Match block ends at its file (main config still valid): %s" % r.stderr.strip())

        def eff(user):
            return subprocess.run([sshd, "-T", "-f", os.path.join(t, "sshd_config"), "-h", key,
                                   "-C", "user=%s,host=h,addr=127.0.0.1" % user], capture_output=True, text=True).stdout.splitlines()
        cap, root, other = eff("coindojo"), eff("root"), eff("someone")
        _check("passwordauthentication yes" in cap and "pubkeyauthentication yes" in cap, "real sshd: coindojo gets passwords, keys still on")
        _check("kbdinteractiveauthentication no" in cap, "real sshd: kbd-interactive stays off for coindojo")
        _check("passwordauthentication no" in root and "permitrootlogin no" in root, "real sshd: root no password, no root login")
        _check("passwordauthentication no" in other, "real sshd: other users stay key-only")


def test_real_sshd_precedence():
    """With real OpenSSH: 00- beats a later `PasswordAuthentication yes` (the live 99-arcade.conf)."""
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if not os.path.exists(sshd) or not shutil.which("ssh-keygen"):
        skip("real sshd precedence (no sshd here)")
        return
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "d")
        os.mkdir(d)
        shutil.copy(os.path.join(ROLE, "files", "00-coin-dojo-keyonly.conf"), d)
        with open(os.path.join(d, "99-arcade.conf"), "w") as fh:
            fh.write("PasswordAuthentication yes\n")
        with open(os.path.join(t, "sshd_config"), "w") as fh:
            fh.write("Include %s/*.conf\nKbdInteractiveAuthentication no\nUsePAM no\n" % d)
        key = os.path.join(t, "hk")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)

        def eff():
            r = subprocess.run([sshd, "-T", "-f", os.path.join(t, "sshd_config"), "-h", key,
                                "-C", "user=coindojo,host=h,addr=127.0.0.1"],
                               capture_output=True, text=True)
            return r.stdout.splitlines()
        lines = eff()
        _check("passwordauthentication no" in lines, "real sshd: 00- drop-in beats 99-arcade yes")
        _check("kbdinteractiveauthentication no" in lines, "real sshd: kbd-interactive off")
        os.remove(os.path.join(d, "00-coin-dojo-keyonly.conf"))
        _check("passwordauthentication yes" in eff(),
               "real sshd control: without the drop-in, 99-arcade yes wins (probe has teeth)")


# ---- deliberately broken copies: each MUST be flagged ------------------------------------------
def test_broken_cases():
    tasks = _read("tasks", "main.yml")
    conf = _read("files", "00-coin-dojo-keyonly.conf")
    defaults = _read("defaults", "main.yml")
    no_full_t = re.sub(r'command: "\{\{ sshd_keyonly_sshd \}\} -t"', 'command: "true"', tasks)
    no_validate = tasks.replace('validate: "{{ sshd_keyonly_sshd }} -t -f %s"', "")
    restart = tasks.replace("state: reloaded", "state: restarted")
    no_guard = tasks.replace("LOCKOUT GUARD", "guard")
    guard_late = tasks.replace("LOCKOUT GUARD", "x") + "\n# LOCKOUT GUARD\n"
    no_post = tasks.replace("PreferredAuthentications=publickey", "PreferredAuthentications=none")
    no_rescue = tasks.replace("rescue:", "always_x:")
    cases = [
        ("task without sshd -t full validation", rule_tasks(no_full_t)),
        ("drop-in copy without sshd -t validate", rule_tasks(no_validate)),
        ("restart instead of reload", rule_tasks(restart)),
        ("no authorized_keys guard", rule_tasks(no_guard)),
        ("authorized_keys guard after the write", rule_tasks(guard_late)),
        ("no new-connection post-check", rule_tasks(no_post)),
        ("no rescue rollback", rule_tasks(no_rescue)),
        ("drop-in says PasswordAuthentication yes",
         rule_dropin(conf.replace("PasswordAuthentication no", "PasswordAuthentication yes"))),
        ("drop-in says KbdInteractiveAuthentication yes",
         rule_dropin(conf.replace("KbdInteractiveAuthentication no", "KbdInteractiveAuthentication yes"))),
        ("backup dir inside sshd_config.d",
         rule_defaults(re.sub(r'(?m)^sshd_keyonly_backup_dir:.*$',
                              'sshd_keyonly_backup_dir: "/etc/ssh/sshd_config.d/bak"', defaults))),
        ("drop-in named 99- (loses to earlier drop-ins)",
         rule_defaults(defaults.replace("00-coin-dojo-keyonly.conf", "99-coin-dojo-keyonly.conf"))),
        ("role not in site.yml", rule_site("- hosts: pios\n  roles:\n    - role: power\n")),
    ]
    cases.append(("backup runs unconditionally",
                  rule_idempotent_backup(tasks.replace("when: sshd_keyonly_will_change | bool\n", "\n"))))
    cases.append(("will_change ignores the checksum",
                  rule_idempotent_backup(tasks.replace("stat.checksum", "stat.mode"))))
    owner = [
        ("owner drop-in relaxes kbd-interactive too",
         tasks.replace("- \"'kbdinteractiveauthentication no' in sshd_keyonly_effective.stdout_lines\"",
                       "- \"'kbdinteractiveauthentication no' in sshd_keyonly_effective.stdout_lines or sshd_keyonly_owner.stat.exists\"")),
        ("password check relaxed unconditionally",
         tasks.replace(" or sshd_keyonly_owner.stat.exists\"", " or true\"")),
        ("the owner drop-in is never looked up", tasks.replace("register: sshd_keyonly_owner\n", "register: x\n")),
        ("the role deletes the owner drop-in",
         tasks + '\n- name: x\n  ansible.builtin.file:\n    path: "{{ sshd_keyonly_owner_dropin }}"\n    state: absent\n'),
    ]
    for name, broken in owner:
        cases.append((name, rule_owner(broken, defaults)))
    cases.append(("owner drop-in named 00- (sorts before key-only)",
                  rule_owner(tasks, defaults.replace("10-coindojo-owner-ssh.conf", "00-coindojo-owner-ssh.conf"))))
    for name, problems in cases:
        _check(bool(problems), "broken case flagged: %s" % name)


if __name__ == "__main__":
    main(globals())
