#!/usr/bin/env python3
"""List the tracked files a linter should see, NUL-separated (ticket #124).

  tools/lintfiles.py sh|yaml|py [REPO]

sh   = tracked *.sh plus tracked extensionless files whose first line is a sh/bash/dash shebang
yaml = tracked *.yml / *.yaml
py   = tracked *.py plus tracked files with a python shebang
Paths are relative to REPO (default: the current directory), so `xargs -0` hands them to a
container that mounts the repo.
"""
import os
import re
import subprocess
import sys

SHEBANG = {"sh": re.compile(rb"^#!\s*(/usr/bin/env\s+(-S\s+)?)?\S*?(ba|da|z)?sh\b"),
           "py": re.compile(rb"^#!\s*(/usr/bin/env\s+(-S\s+)?)?\S*python")}
EXT = {"sh": (".sh",), "yaml": (".yml", ".yaml"), "py": (".py",)}


def tracked(repo):
    out = subprocess.run(["git", "-C", repo, "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode().split("\0") if p]


def select(kind, repo="."):
    found = []
    for p in tracked(repo):
        full = os.path.join(repo, p)
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        if p.endswith(EXT[kind]):
            found.append(p)
        elif kind in SHEBANG and "." not in os.path.basename(p):
            with open(full, "rb") as f:
                if SHEBANG[kind].match(f.readline()):
                    found.append(p)
    return found


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in EXT:
        sys.exit("usage: lintfiles.py sh|yaml|py [REPO]")
    sys.stdout.write("".join(p + "\0" for p in select(sys.argv[1], *sys.argv[2:])))
