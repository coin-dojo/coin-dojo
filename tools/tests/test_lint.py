#!/usr/bin/env python3
"""Tests for `make lint` / `make secrets` (#124): tools/lintfiles.py, tools/secrets-scan.sh, the configs, mk/lint.mk.

Run: python3 tools/tests/test_lint.py   (stdlib only; needs Docker/Colima for the container cases, else they print SKIP)
The tools run in the pinned Ansible image (ansible/bin/ans --image), on throwaway repos under ~/.cache/coin-dojo/ansible.
Broken cases (each must FAIL its tool): a planted shellcheck error, a planted yamllint error, a planted unused import,
a planted OBVIOUSLY FAKE token (HKFAKE-NOT-A-SECRET-..., a custom gitleaks rule in .gitleaks.toml) in the working copy and,
separately, only in history, and a fake private key block (body "NOTAREALKEY" repeated; matches gitleaks' default rule).
No real credential or vendor prefix appears here.
Last: the repo itself passes `make lint` and `make secrets`.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, finish, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)



def failure_output(r):
    """Everything a failing `make lint` / `make secrets` printed, stdout then stderr (#159: never only the tail)."""
    return "%s\n--- stderr ---\n%s" % (r.stdout.rstrip("\n"), r.stderr.rstrip("\n"))

def sh(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def git(d, *args):
    r = sh("git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args)
    assert r.returncode == 0, r.stderr
    return r


def docker_up():
    return shutil.which("docker") and sh("docker", "info").returncode == 0


def image():
    r = sh(str(REPO / "ansible/bin/ans"), "true")        # builds the image on first use / after a pin change
    if r.returncode != 0:
        return None
    return sh(str(REPO / "ansible/bin/ans"), "--image").stdout.strip()


def run_in(img, d, *cmd, env=None):
    envs = [x for k, v in (env or {}).items() for x in ("-e", f"{k}={v}")]
    return sh("docker", "run", "--rm", "-v", f"{d}:{d}", "-v", f"{REPO}:{REPO}:ro", *envs, "-w", str(d), img, *cmd)


def fresh(parent, name, files, config=True):
    d = parent / name
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    if config:
        for c in (".shellcheckrc", ".yamllint", "ruff.toml", ".gitleaks.toml"):
            shutil.copy(REPO / c, d / c)
    for rel, body in files.items():
        (d / rel).write_text(body)
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "init")
    return d


FAKE_TOKEN = "HKFAKE-NOT-A-SECRET-" + "A1B2C3D4E5F6"
# assembled from parts so this file itself does not look like a key block to the scan
FAKE_KEY = "-----BEGIN RSA PRIVATE " + "KEY-----\n" + ("NOTAREALKEY" * 8) + "\n-----END RSA PRIVATE " + "KEY-----\n"

# --- 1. file lists (no Docker) ------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as t:
    d = fresh(pathlib.Path(t), "lf", {"a.sh": "echo\n", "tool": "#!/usr/bin/env bash\necho\n", "pytool": "#!/usr/bin/env python3\n",
                                      "b.py": "x = 1\n", "c.yml": "a: 1\n", "d.yaml": "a: 1\n", "README.md": "#!/bin/sh\n", "data": "plain\n"},
              config=False)
    (d / "untracked.sh").write_text("echo\n")
    got = {k: sorted(p for p in sh(sys.executable, str(REPO / "tools/lintfiles.py"), k, str(d)).stdout.split("\0") if p)
           for k in ("sh", "py", "yaml")}
    check(got["sh"] == ["a.sh", "tool"], f"lintfiles sh: tracked .sh + shebang scripts only, got {got['sh']}")
    check(got["py"] == ["b.py", "pytool"], f"lintfiles py: .py + python shebang, got {got['py']}")
    check(got["yaml"] == ["c.yml", "d.yaml"], f"lintfiles yaml, got {got['yaml']}")
    check(sh(sys.executable, str(REPO / "tools/lintfiles.py"), "bogus").returncode != 0, "lintfiles: an unknown kind fails")

# --- 2. make wiring (no Docker) -----------------------------------------------------------------------------------
r = sh("make", "-C", str(REPO), "-n", "lint")
check(r.returncode == 0 and all(w in r.stdout for w in ("shellcheck", "yamllint", "ruff", "ansible-lint")),
      "make -n lint runs shellcheck, yamllint, ruff and ansible-lint")
r = sh("make", "-C", str(REPO), "-n", "secrets")
check(r.returncode == 0 and "secrets-scan.sh" in r.stdout, "make -n secrets runs tools/secrets-scan.sh")
check(sh("make", "-C", str(REPO), "lint", "LINT_ONLY=bogus").returncode != 0, "LINT_ONLY=bogus is refused")
check((REPO / "ansible/.ansible-lint").is_file() and "var-naming[no-role-prefix]" in (REPO / "ansible/.ansible-lint").read_text(),
      "ansible/.ansible-lint exists and every skip names its rule")

# --- 3. the tools, in the pinned image ----------------------------------------------------------------------------
if not docker_up():
    skip("Docker is not running (colima start): container cases not run")
else:
    img = image()
    check(img is not None, "the pinned Ansible image builds")
    home = pathlib.Path.home() / ".cache/coin-dojo/ansible"
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home, prefix="lint-test.") as t:
        t = pathlib.Path(os.path.realpath(t))
        for tool in ("shellcheck --version", "yamllint --version", "ruff --version", "ansible-lint --version", "gitleaks version"):
            check(run_in(img, t, *tool.split()).returncode == 0, f"image has {tool.split()[0]}")

        good = fresh(t, "good", {"ok.sh": '#!/bin/sh\necho "$1"\n', "ok.yml": "a: 1\n", "ok.py": "import os\nprint(os.name)\n"})
        bad = fresh(t, "bad", {"bad.sh": "#!/bin/sh\nfor f in $1; do echo $f; done\n", "bad.yml": "a: 1\n b:   2\n", "bad.py": "import os\n"})
        check(run_in(img, good, "shellcheck", "-x", "ok.sh").returncode == 0, "shellcheck passes a clean script")
        check(run_in(img, bad, "shellcheck", "-x", "bad.sh").returncode != 0, "BROKEN CASE: a planted shellcheck error (SC2086) FAILS")
        check(run_in(img, good, "yamllint", "-s", "-c", ".yamllint", "ok.yml").returncode == 0, "yamllint passes a clean file")
        check(run_in(img, bad, "yamllint", "-s", "-c", ".yamllint", "bad.yml").returncode != 0, "BROKEN CASE: a planted yamllint error FAILS")
        check(run_in(img, good, "ruff", "check", "--no-cache", "--config", "ruff.toml", "ok.py").returncode == 0, "ruff passes a clean file")
        check(run_in(img, bad, "ruff", "check", "--no-cache", "--config", "ruff.toml", "bad.py").returncode != 0,
              "BROKEN CASE: a planted unused import FAILS ruff (F401)")

        scan = [str(REPO / "tools/secrets-scan.sh")]
        clean = fresh(t, "clean", {"a.txt": "nothing here\n"})
        r = run_in(img, clean, "sh", *scan, str(clean))
        check(r.returncode == 0, "secrets-scan passes a clean repo (history + working copy)")
        for label, body in (("fake token", f"token = {FAKE_TOKEN}\n"), ("fake private key block", FAKE_KEY)):
            leak = fresh(t, "leak-" + label.split()[1], {"conf.txt": body})
            r = run_in(img, leak, "sh", *scan, str(leak))
            check(r.returncode != 0, f"BROKEN CASE: a planted {label} FAILS the scan")
            check(FAKE_TOKEN not in r.stdout + r.stderr and "NOTAREALKEY" * 2 not in r.stdout + r.stderr, f"the scan output redacts the {label}")
        # only in history: committed, then removed from the working copy
        hist = fresh(t, "hist", {"conf.txt": f"token = {FAKE_TOKEN}\n"})
        (hist / "conf.txt").write_text("gone\n")
        git(hist, "commit", "-q", "-am", "remove it")
        check(run_in(img, hist, "sh", *scan, str(hist)).returncode != 0, "BROKEN CASE: a fake token only in history FAILS the scan")
        # only in the working copy, never committed
        wc = fresh(t, "wc", {"a.txt": "x\n"})
        (wc / "new.txt").write_text(f"token = {FAKE_TOKEN}\n")
        check(run_in(img, wc, "sh", *scan, str(wc)).returncode != 0, "BROKEN CASE: an uncommitted fake token FAILS the scan")
        # the test_tickets.py allowlist is by match: another secret on the same line is still caught
        same = fresh(t, "same", {"x.txt": f"curl -u bot:hunter22 http://x {FAKE_TOKEN}\n"})
        check(run_in(img, same, "sh", *scan, str(same)).returncode != 0, "BROKEN CASE: a fake token next to an allowlisted literal FAILS")
        # a failing clone fails closed
        check(run_in(img, t, "sh", *scan, str(t / "nope")).returncode != 0, "secrets-scan fails closed on a missing repo")

    # --- #159: a failing run prints its whole output (the old tail slice hid a ruff F811 at the top) -------------
    class _R:  # noqa: D401 - a CompletedProcess stand-in
        stdout = "ruff F811 redefinition at the top\n" + "x" * 5000 + "\nRESULT: FAIL lint\n"
        stderr = "make: *** [lint] Error 1\n"
    out = failure_output(_R())
    check("ruff F811 redefinition at the top" in out and "Error 1" in out, "#159: a failing run prints its output in full")
    check("ruff F811 redefinition at the top" not in (_R.stdout[-3000:] + _R.stderr[-1000:]),
          "#159 BROKEN case: the old tail slice would have hidden the cause")

    # --- 4. the repo itself ---------------------------------------------------------------------------------------
    r = sh("make", "-C", str(REPO), "lint")
    check(r.returncode == 0 and r.stdout.rstrip().endswith("RESULT: PASS lint"), "make lint passes on this repo")
    if r.returncode != 0:
        print(failure_output(r))
    r = sh("make", "-C", str(REPO), "secrets")
    check(r.returncode == 0 and r.stdout.rstrip().endswith("RESULT: PASS secrets"), "make secrets passes on this repo")
    if r.returncode != 0:
        print(failure_output(r))

sys.exit(finish())
