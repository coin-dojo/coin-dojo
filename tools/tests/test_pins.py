#!/usr/bin/env python3
"""Supply-chain pins (#166, security review #138 L5): every registry base image is pinned by digest, and the PyYAML
the config loader installs is pinned by version + sha256 (pip --require-hashes).

Rules work on TEXT, so the broken cases (a tag-only FROM, an unhashed requirement) run against planted copies.
Pure stdlib. Run directly:  python3 tools/tests/test_pins.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main  # noqa: E402

# Images this repo builds itself from a pinned Dockerfile (a local tag, never pulled from a registry).
LOCAL_IMAGE = re.compile(r"^coin-dojo-[a-z0-9-]+:")


def from_problems(text):
    """FROM lines of a Dockerfile that pull a registry image without @sha256 (stage names and local images are fine)."""
    stages, bad = set(), []
    for m in re.finditer(r"(?im)^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", text):
        image, stage = m.group(1), m.group(2)
        if image in stages or image == "scratch" or LOCAL_IMAGE.match(image):
            pass
        elif not re.search(r"@sha256:[0-9a-f]{64}$", image):
            bad.append(image)
        if stage:
            stages.add(stage)
    return bad


def requirement_problems(text):
    """Requirement entries without an exact == pin or without a sha256 hash."""
    joined = re.sub(r"\\\n", " ", "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
    bad = []
    for line in filter(str.strip, joined.splitlines()):
        name = line.split()[0]
        if "==" not in name:
            bad.append("%s: not pinned with ==" % name)
        if not re.search(r"--hash=sha256:[0-9a-f]{64}", line):
            bad.append("%s: no sha256 hash" % name)
    return bad


def dockerfiles():
    out = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split()
    return [p for p in out if os.path.basename(p) == "Dockerfile"]


def test_every_from_is_pinned_by_digest():
    files = dockerfiles()
    check(len(files) >= 4, "found the repo's Dockerfiles (%d)" % len(files))
    for p in files:
        bad = from_problems(open(os.path.join(ROOT, p)).read())
        check(bad == [], "%s: every registry FROM has @sha256 (tag-only: %s)" % (p, bad or "none"))


def local_tag_problems(dockerfile_text, esde_dockerfile_bytes):
    """A FROM coin-dojo-esde-preview:<tag> must name the CURRENT esde Dockerfile's tag (preview-esde.sh: sha256, 8 chars):
    pinning the esde base re-tags the image, and a stale tag builds on an old local image or none at all."""
    import hashlib
    want = hashlib.sha256(esde_dockerfile_bytes).hexdigest()[:8]
    return ["%s (current: %s)" % (t, want) for t in re.findall(r"(?m)^FROM\s+coin-dojo-esde-preview:(\S+)", dockerfile_text) if t != want]


def test_local_preview_tags_are_current():
    esde = open(os.path.join(ROOT, "skills", "local-preview", "esde", "Dockerfile"), "rb").read()
    for p in dockerfiles():
        bad = local_tag_problems(open(os.path.join(ROOT, p)).read(), esde)
        check(bad == [], "%s: coin-dojo-esde-preview tag matches the esde Dockerfile %s" % (p, bad or ""))
    check(local_tag_problems("FROM coin-dojo-esde-preview:96df5f4a\n", esde) != [], "BROKEN case: a stale preview tag is flagged")


def test_broken_tag_only_from_is_caught():
    check(from_problems("FROM python:3.13.2-slim-bookworm\n") == ["python:3.13.2-slim-bookworm"],
          "BROKEN case: a tag-only FROM is flagged")
    check(from_problems("FROM debian:trixie@sha256:abc\n") == ["debian:trixie@sha256:abc"], "BROKEN case: a short digest is flagged")
    ok = "FROM a/b:1@sha256:%s AS tools\nFROM tools\nFROM coin-dojo-esde-preview:96df5f4a\nFROM scratch\n" % ("0" * 64)
    check(from_problems(ok) == [], "a digest pin, a stage name, a local coin-dojo-* image and scratch pass")


def test_config_venv_pyyaml_is_hashed():
    req = open(os.path.join(ROOT, "mk", "config-requirements.txt")).read()
    check(requirement_problems(req) == [], "mk/config-requirements.txt: pinned + hashed %s" % requirement_problems(req))
    check(len(re.findall(r"--hash=sha256:", req)) > 1, "every published PyYAML file is hashed (any platform can install)")
    mk = open(os.path.join(ROOT, "mk", "config.mk")).read()
    check("--require-hashes -r" in mk and "config-requirements.txt" in mk and not re.search(r"pip install[^\n]*\bpyyaml\b", mk),
          "make config-venv installs only from the hashed file")


def test_broken_unhashed_requirement_is_caught():
    check(requirement_problems("pyyaml\n") == ["pyyaml: not pinned with ==", "pyyaml: no sha256 hash"],
          "BROKEN case: a bare `pyyaml` is flagged")
    check(requirement_problems("pyyaml==6.0.3\n") == ["pyyaml==6.0.3: no sha256 hash"], "BROKEN case: a pin without a hash is flagged")
    good = "# c\npyyaml==6.0.3 \\\n    --hash=sha256:%s\n" % ("a" * 64)
    check(requirement_problems(good) == [], "a pinned, hashed entry across a line continuation passes")


if __name__ == "__main__":
    main(globals())
