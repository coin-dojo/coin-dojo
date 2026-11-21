#!/usr/bin/env python3
"""build-fetch (#108): fetch the pinned build outputs the VM's retroarch122 role copies, READ-ONLY, from the cabinet.

Reads the artifact list (src, dest, sha256) from ansible/roles/retroarch122/defaults/main.yml, scp's each `dest`
from the cabinet into the `src` under build/*/out, and keeps it only if its sha256 matches the pin. A mismatch
is deleted and refused (exit 1). Nothing is ever written on the cabinet: scp reads only.
  --dry-run   print what would be fetched
Env (tests): BUILD_FETCH_SCP = the copy command (default `scp -p`), BUILD_FETCH_HOST (default hk_ssh_user@hk_cabinet_host from the config),
BUILD_FETCH_DEFAULTS = another defaults file, BUILD_FETCH_ROOT = another repo root.
"""
import hashlib, os, pathlib, re, shlex, subprocess, sys, tempfile

ROOT = pathlib.Path(os.environ.get("BUILD_FETCH_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
DEFAULTS = pathlib.Path(os.environ.get("BUILD_FETCH_DEFAULTS") or ROOT / "ansible/roles/retroarch122/defaults/main.yml")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import hkconfig  # noqa: E402  (tools/hkconfig.py: the one way a script reads the config)
SCP = shlex.split(os.environ.get("BUILD_FETCH_SCP", "scp -p -o BatchMode=yes"))


def pins(text):
    """[(src, dest, sha256)] of the retroarch122_artifacts list, vars resolved."""
    scalars = dict(re.findall(r'(?m)^(\w+):\s*"?([^"\n#]+?)"?\s*$', text))
    scalars["hk_repo_root"] = str(ROOT)

    def sub(v):
        for _ in range(5):
            v = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: scalars.get(m.group(1), m.group(0)), v)
        return v

    block = text.split("retroarch122_artifacts:", 1)[1]
    block = re.split(r"(?m)^\S", block, maxsplit=1)[0]
    out = []
    for item in re.split(r"(?m)^\s*- (?=src:)", block)[1:]:
        f = dict(re.findall(r'(?m)^\s*(?:- )?(src|dest|sha256):\s*"?([^"\n]+?)"?\s*$', "- " + item))
        out.append((sub(f["src"]), sub(f["dest"]), f["sha256"]))
    return out


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cabinet():
    """user@host of the cabinet: BUILD_FETCH_HOST (tests), else hk_ssh_user@hk_cabinet_host from the config. Never a default host."""
    h = os.environ.get("BUILD_FETCH_HOST", "").strip()
    if h:
        return h
    try:
        host, user = hkconfig.get("hk_cabinet_host"), hkconfig.get("hk_ssh_user")
    except hkconfig.ConfigUnavailable as e:
        print(f"build-fetch: cannot read the configuration: {e} (make config-check)", file=sys.stderr)
        raise SystemExit(1)
    if not host:
        print("build-fetch: no cabinet: set hk_cabinet_host in config/local.yml (docs/CONFIG.md)", file=sys.stderr)
        raise SystemExit(1)
    return f"{user}@{host}" if user else host


def main(argv):
    global HOST
    HOST = cabinet()
    dry = "--dry-run" in argv
    items = pins(DEFAULTS.read_text())
    if not items:
        print("build-fetch: no artifacts found in", DEFAULTS, file=sys.stderr)
        return 1
    bad = 0
    for src, dest, want in items:
        src = pathlib.Path(src)
        if dry:
            print(f"build-fetch: would fetch {HOST}:{dest} -> {src} (sha256 {want[:12]})")
            continue
        if src.is_file() and sha(src) == want:
            print(f"build-fetch: {src.relative_to(ROOT)} already OK (sha256 {want[:12]})")
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=src.parent) as td:
            tmp = pathlib.Path(td) / src.name
            r = subprocess.run(SCP + [f"{HOST}:{dest}", str(tmp)])
            if r.returncode != 0 or not tmp.is_file():
                print(f"build-fetch: FAILED to fetch {dest} from {HOST}", file=sys.stderr)
                bad += 1
                continue
            got = sha(tmp)
            if got != want:
                print(f"build-fetch: REFUSED {dest}: sha256 {got}, pinned {want}", file=sys.stderr)
                bad += 1
                continue
            os.chmod(tmp, 0o644 if src.name == "BUILDINFO" else 0o755)
            os.replace(tmp, src)
            print(f"build-fetch: {src.relative_to(ROOT)} fetched, sha256 OK ({want[:12]})")
    print("RESULT: " + ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
