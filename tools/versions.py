#!/usr/bin/env python3
"""versions (#278): one generated list of every pinned third-party component -> versions.lock.

    tools/versions.py generate [--check]       write versions.lock from the pin sources (--check: exit 1 if stale)
    tools/versions.py show                     the table on screen, with file:line of every pin
    tools/versions.py bump C V [--sha S] [--commit X] [--url U]
                                               change component C to version V in EVERY pin source that names it,
                                               regenerate versions.lock, add the CHANGELOG `### Versions` line
    tools/versions.py changes BASE_LOCK        the components that differ from an older lock (text on stdout)
    make versions / make bump C=<component> V=<version> [SHA=...] [COMMIT=...]

The pin sources stay the truth (build scripts, pins.json, role defaults, Dockerfiles, CI scripts); this file only
knows WHERE each pin sits: one regex per field, one capture group each, optionally inside a `region` of the file.
Every capture of a field in every location of a component must agree, else generation stops (a half-done bump).
`pinned_in` names file + anchor, not a line number: a line number moves with every unrelated edit and would make
the lock stale for nothing (`show` prints the lines).

sha256 fields may carry a tag (`sha256:x86_64`) when one version has several files; bump then takes
--sha tag=value,tag=value. A component without a version field uses its commit as the version.
Coverage: tests/test_versions.py scans every tracked file for pin-looking values (sha256, @sha256 digests, 40-hex
commits, `==` requirement pins) and fails on one that no component and no OWN/NOT_PINS entry explains.
Stdlib only.
"""
import hashlib
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = "versions.lock"
H64, H40 = r"([0-9a-f]{64})", r"([0-9a-f]{40})"
FIELDS = ("version", "commit", "sha256", "source")


def L(file, at, region=None, optional=False, unless_exists=None, when=None, **fields):
    """A pin location: file, a human anchor, an optional region regex (DOTALL) and field -> regex or [regex].
    optional: skipped while the file does not exist; when: a regex (MULTILINE) the file must contain, else the
    location is skipped (a pin another branch is about to add to a file that exists already)."""
    return {"file": file, "at": at, "region": region, "optional": optional, "unless_exists": unless_exists,
            "when": when, "fields": {k.replace("__", ":"): (v if isinstance(v, list) else [v]) for k, v in fields.items()}}


def C(name, what, *locs, multi=None, needs_url=None, checks=None, note=None):
    """needs_url: bump refuses without URL= (why); checks: [(fn(vals) -> problem or None, what)];
    note: printed after a bump (what it cannot change by itself)."""
    return {"name": name, "what": what, "locs": list(locs), "multi": multi or {}, "needs_url": needs_url,
            "checks": checks or [], "note": note}


# The licence manifest (#254) names the exact source of every component the image redistributes: its `ref` (and the
# commit inside raw licence URLs) must follow the build pin (licences.py check), so it is one more place of that pin.
# The licence texts' own sha256 are OWN below: they follow the ref, re-read with licences.py after a bump.
MANIFEST = "skills/build-image/licences/components.json"
LIC_NOTE = ("re-read the licence texts at the new ref (skills/build-image/scripts/licences.py) and update the "
            "manifest's human `version` text in " + MANIFEST)
ESDE_LIBS = "skills/build-image/licences/esde-libraries.tsv"   # #276: the libraries inside the pinned ES-DE AppImage
LIC_COMMIT = r'(?:"ref": "|raw\.githubusercontent\.com/[^/"]+/[^/"]+/)' + H40


def manifest_entry(key, value):
    """Region: the manifest object whose `key` is `value` (objects nest one level: licence_files)."""
    return r'\{[^{}]*?"%s": "%s",(?:[^{}]|\{[^{}]*\})*\}' % (key, re.escape(value))


def LIC(key, value, **fields):
    return L(MANIFEST, "%s=%s" % (key, value), region=manifest_entry(key, value), **fields)


DEB_TRIXIE = r'^BASE_IMAGE="debian:(trixie)@sha256:[0-9a-f]{64}"'
DEB_TRIXIE_SHA = r'^BASE_IMAGE="debian:trixie@sha256:' + H64 + '"'
FC_PINS = "build/fightcade/pins.json"   # #267: the ONE Fightcade source (fetch.sh, the role, first launch read it)
FC_NOTE = "set `size` (bytes of the new file) in " + FC_PINS + ": bump does not download"


def fc_region(key):
    return r'"%s"\s*:\s*\{[^}]*\}' % key


COMPONENTS = [
    # ---- emulators built from source (build/*/build.sh) ----
    C("retroarch", "RetroArch, built from source (build/retroarch)",
      L("build/retroarch/build.sh", "RA_TAG", version=r'^RA_TAG="([^"]+)"', commit=r'^RA_COMMIT="' + H40 + '"',
        source=r'^RA_REPO="([^"]+)"'),
      LIC("id", "retroarch", commit=LIC_COMMIT), note=LIC_NOTE),
    C("mupen64plus-next", "mupen64plus-libretro-nx N64 core, built from source (build/n64)",
      L("build/n64/build.sh", "MUPEN_COMMIT", commit=r'^MUPEN_COMMIT="' + H40 + '"', source=r'^MUPEN_REPO="([^"]+)"'),
      LIC("id", "mupen64plus_next", commit=LIC_COMMIT), note=LIC_NOTE),
    C("stella", "Stella 2600 core, built from source (build/stella)",
      L("build/stella/build.sh", "STELLA_TAG", version=r'^STELLA_TAG="([^"]+)"', commit=r'^STELLA_COMMIT="' + H40 + '"',
        source=r'^STELLA_REPO="([^"]+)"'),
      LIC("id", "stella", commit=LIC_COMMIT, version=r'"version": "([^"]+)"'), note=LIC_NOTE),
    C("flycast", "flycast standalone (Dreamcast/Naomi), built from source (build/flycast)",
      L("build/flycast/build.sh", "FLYCAST_TAG", version=r'^FLYCAST_TAG="([^"]+)"', source=r'^FLYCAST_REPO="([^"]+)"'),
      LIC("id", "flycast", version=r'"tag": "([^"]+)"', commit=LIC_COMMIT), note=LIC_NOTE),
    C("box64", "Box64, built from source for Fightcade mode (build/box64)",
      L("build/box64/build.sh", "BOX64_TAG", version=r'^BOX64_TAG="([^"]+)"', commit=r'^BOX64_COMMIT="' + H40 + '"',
        source=r'^BOX64_REPO="([^"]+)"'),
      L("build/box64/README.md", "intro", version=r"\*\*Box64 (v[0-9.]+)\*\*", commit=r"\(commit `" + H40 + r"`\)"),
      LIC("id", "box64", commit=LIC_COMMIT), note=LIC_NOTE),
    C("debian-trixie-build", "build container base image for retroarch, n64, box64 and the pinned cores (arm64)",
      L("build/retroarch/build.sh", "BASE_IMAGE", version=DEB_TRIXIE, sha256=DEB_TRIXIE_SHA),
      L("build/n64/build.sh", "BASE_IMAGE", version=DEB_TRIXIE, sha256=DEB_TRIXIE_SHA),
      L("build/box64/build.sh", "BASE_IMAGE", version=DEB_TRIXIE, sha256=DEB_TRIXIE_SHA),
      L("build/cores/core.sh", "BASE_IMAGE", optional=True, version=DEB_TRIXIE, sha256=DEB_TRIXIE_SHA)),   # #274
    C("debian-trixie-build-unpinned", "build container base image for flycast, stella (tag only, no digest)",
      L("build/flycast/build.sh", "BASE_IMAGE", version=r'^BASE_IMAGE="debian:([a-z0-9.-]+)"$'),
      L("build/stella/build.sh", "BASE_IMAGE", version=r'^BASE_IMAGE="debian:([a-z0-9.-]+)"$')),
    # ---- Fightcade mode downloads: build/fightcade/pins.json (#267); the role keeps the patched main.js sha256 ----
    C("wine", "Wine (amd64 wow64) for Fightcade mode",
      L(FC_PINS, "wine", region=fc_region("wine"),
        version=[r'"url":\s*"[^"]*/download/([^/"]+)/', r"wine-([0-9.]+)-amd64", r'"title":\s*"Wine ([0-9.]+)"'],
        sha256=r'"sha256":\s*"' + H64 + '"', source=r'"url":\s*"([^"]+)"'), note=FC_NOTE),
    C("electron", "Electron (linux-arm64) for the Fightcade lobby",
      L(FC_PINS, "electron", region=fc_region("electron"),
        version=[r"/download/v([0-9.]+)/", r"electron-v([0-9.]+)-linux", r'"title":\s*"Electron ([0-9.]+)"'],
        sha256=r'"sha256":\s*"' + H64 + '"', source=r'"url":\s*"([^"]+)"'), note=FC_NOTE),
    C("fightcade", "Fightcade 2 linux client (archive + its lib/main.js the role patches)",
      L(FC_PINS, "fightcade", region=fc_region("fightcade"),
        version=[r'"version":\s*"([^"]+)"', r"Fightcade-linux-v([0-9.]+)\.tar", r'"title":\s*"Fightcade ([0-9.]+)"'],
        sha256__archive=r'"sha256":\s*"' + H64 + '"', source=r'"url":\s*"([^"]+)"'),
      L("ansible/roles/fightcade/defaults/main.yml", "fightcade_archives[fightcade].patches",
        region=r"  # Fightcade [0-9.]+\. The folders.*?(?=\n# Box64)", version=r"# Fightcade ([0-9.]+)\. The folders",
        sha256__main_js=r"^        sha256: " + H64), note=FC_NOTE),
    # ---- the image and the test VM ----
    C("raspios-lite-arm64", "base image: Raspberry Pi OS Lite arm64 (make base-image)",
      L("skills/build-image/scripts/base-image.sh", "PIN_URL", version=r"^PIN_URL=\S*/raspios_lite_arm64-([0-9-]+)/",
        file_date=r"^PIN_URL=\S*/([0-9]{4}-[0-9]{2}-[0-9]{2})-raspios-[a-z]+-arm64-lite\.img\.xz$",
        sha256=r"^PIN_SHA256=" + H64, source=r"^PIN_URL=(\S+)"),
      needs_url="the release folder's date and the image file's date can differ (raspios_lite_arm64-<folder>/<file>-"
                "raspios-<suite>-arm64-lite.img.xz)",
      checks=[(lambda v: None if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.get("version", "")) else
               "the folder date %r is not YYYY-MM-DD" % v.get("version"), "folder date")]),
    C("es-de", "ES-DE frontend AppImage (image build and test VM)",
      L("skills/build-image/scripts/chroot-setup.sh", "ES-DE curl", version=r"packages\.es-de\.org/release/([0-9.]+)/",
        source=r"(https://packages\.es-de\.org/release/[0-9.]+/ES-DE_aarch64\.AppImage)"),
      L("ansible/roles/vm_base/defaults/main.yml", "vm_base_esde_url", version=r"^vm_base_esde_url: \S*/release/([0-9.]+)/",
        sha256=r"^vm_base_esde_sha256: " + H64, source=r"^vm_base_esde_url: (\S+)"),
      LIC("id", "es-de", version=r'(?:"version": "|"ref": "v|/-/raw/v|"download": "[^"]*/release/)([0-9.]+)'),
      L(ESDE_LIBS, "header", version=r"^# ES-DE ([0-9.]+) AppImage", sha256=r"^# ES-DE [0-9.]+ AppImage \(\S+, sha256 " + H64),
      note=LIC_NOTE + "; re-inventory the libraries the new AppImage bundles in " + ESDE_LIBS + " (#276)"),
    C("glsl-shaders", "libretro/glsl-shaders at a commit (crt-pi, stock), fetched by role retroarch122",
      L("ansible/roles/retroarch122/defaults/main.yml", "retroarch122_shader_commit",
        commit=r"^retroarch122_shader_commit: " + H40,
        sha256__crt_pi_glslp=r"path: crt/crt-pi\.glslp\n\s+sha256: " + H64,
        sha256__crt_pi_glsl=r"path: crt/shaders/crt-pi\.glsl\n\s+sha256: " + H64,
        sha256__stock_glsl=r"path: stock\.glsl\n\s+sha256: " + H64,
        source=r'^retroarch122_shader_base_url: "(https://[^"{]+)'),
      L("skills/build-image/scripts/chroot-setup.sh", "GLSL_COMMIT", when=r'^GLSL_COMMIT="',   # #274 pins the tarball
        commit=r'^GLSL_COMMIT="' + H40 + '"', sha256__tarball=r'^GLSL_SHA256="' + H64 + '"'),
      LIC("id", "glsl-shaders", commit=LIC_COMMIT)),
    C("libretro-buildbot-cores", "the other libretro cores: buildbot aarch64 nightly `latest` (NOT pinned upstream)",
      L("ansible/roles/cores/defaults/main.yml", "cores_buildbot", version=r"^cores_buildbot: \S+/(latest)",
        source=r"^cores_buildbot: (\S+)")),
    # ---- container images ----
    C("debian-12-host", "host-side helper image for prep/verify (lib-host.sh HK_DEBIAN_IMAGE)",
      L("skills/build-image/scripts/lib-host.sh", "HK_DEBIAN_IMAGE", version=r"HK_DEBIAN_IMAGE:-debian:([0-9a-z.-]+)@",
        sha256=r"HK_DEBIAN_IMAGE:-debian:[0-9a-z.-]+@sha256:" + H64)),
    C("binfmt", "tonistiigi/binfmt: qemu-aarch64 for x86_64 build hosts (lib-host.sh HK_BINFMT_IMAGE)",
      L("skills/build-image/scripts/lib-host.sh", "HK_BINFMT_IMAGE", version=r"tonistiigi/binfmt:([^@]+)@",
        sha256=r"tonistiigi/binfmt:[^@]+@sha256:" + H64)),
    C("debian-trixie-slim-preview", "preview containers (controls, ES-DE, RetroArch 1.22 previews)",
      *[L(f, "FROM", version=r"^FROM debian:([^@\s]+)@", sha256=r"^FROM debian:[^@\s]+@sha256:" + H64)
        for f in ("skills/controls/preview/Dockerfile", "skills/local-preview/esde/Dockerfile",
                  "skills/local-preview/retroarch-122/Dockerfile")]),
    C("python-ansible-image", "the Ansible/lint control container base (ansible/Dockerfile)",
      L("ansible/Dockerfile", "FROM python", version=r"^FROM python:([^@\s]+)@", sha256=r"^FROM python:[^@\s]+@sha256:" + H64)),
    C("shellcheck", "shellcheck static binary (ansible/Dockerfile stage)",
      L("ansible/Dockerfile", "FROM shellcheck", version=r"^FROM koalaman/shellcheck-alpine:([^@\s]+)@",
        sha256=r"^FROM koalaman/shellcheck-alpine:[^@\s]+@sha256:" + H64)),
    C("gitleaks", "gitleaks static binary (ansible/Dockerfile stage)",
      L("ansible/Dockerfile", "FROM gitleaks", version=r"^FROM zricethezav/gitleaks:([^@\s]+)@",
        sha256=r"^FROM zricethezav/gitleaks:[^@\s]+@sha256:" + H64)),
    # ---- Python / Ansible tooling ----
    *[C(pkg, "pip pin in ansible/requirements.txt (the Ansible/lint container)",
        L("ansible/requirements.txt", pkg, version=r"^%s==(\S+)" % re.escape(pkg)))
      for pkg in ("ansible-core", "ansible-lint", "yamllint", "ruff")],
    *[C(col, "Ansible collection in ansible/requirements.yml",
        L("ansible/requirements.yml", col, version=r'- name: %s\n\s+version: "([^"]+)"' % re.escape(col)))
      for col in ("community.general", "ansible.posix")],
    C("pyyaml-config", "PyYAML for the config loader venv (make config-venv; pip --require-hashes)",
      L("mk/config-requirements.txt", "pyyaml==", version=r"^pyyaml==(\S+)"), multi={"sha256": ("mk/config-requirements.txt",
                                                                                           r"--hash=sha256:" + H64)}),
]



def core_components(root=ROOT):
    """#274: one component per libretro core built from a pinned commit (build/<dir>/build.sh with CORE= and
    CORE_COMMIT=, steps in build/cores/core.sh). Found by globbing, so a new core build needs no edit here; its
    manifest entry ("build": "build/<dir>") is the second place of the commit. Named after CORE (the core's name)."""
    out = []
    for p in sorted(pathlib.Path(root).glob("build/*/build.sh")):
        text = p.read_text()
        core = re.search(r'^CORE="([A-Za-z0-9_.-]+)"', text, re.M)
        if not core or not re.search(r'^CORE_COMMIT=', text, re.M):
            continue
        rel = p.relative_to(root).as_posix()
        out.append(C(core.group(1), "%s libretro core, built from a pinned commit (%s)" % (core.group(1), rel.rsplit("/", 1)[0]),
                     L(rel, "CORE_COMMIT", commit=r'^CORE_COMMIT="' + H40 + '"', source=r'^CORE_REPO="([^"]+)"'),
                     LIC("build", rel.rsplit("/", 1)[0], commit=LIC_COMMIT), note=LIC_NOTE))
    return out


COMPONENTS += core_components()

# Values in pin-source files that are NOT third-party versions: hashes of our own files (patches, configs we ship,
# our own build outputs, whose version is the component above). file -> [regex whose group 1 is the value].
OWN = {
    "build/flycast/build.sh": [r"\.patch=" + H64],
    "build/retroarch/build.sh": [r"\.patch=" + H64],
    "ansible/roles/retroarch122/defaults/main.yml": [r"sha256: " + H64],   # our build outputs + our N64 cfg/rmp
    MANIFEST: [r'"sha256": "' + H64 + '"',           # licence texts at the pinned ref (licences.py fetch/check)
               r'raw\.githubusercontent\.com/[^/"]+/[^/"]+/' + H40],   # the commit a buildbot core's licence was read at
    ESDE_LIBS: [r"\t" + H64 + "$"],                   # each bundled library's sha256 inside that AppImage (licences.py build)
}
# Tracked files with pin-looking values that are not pins at all (path or glob -> why).
NOT_PINS = {
    "*/tests/*": "test fixtures",
    "tests/*": "repo-wide tests",
    "personal/*": "never exported; the maintainer's own material",
    ".claude/*": "team notes",
    "CHANGELOG.md": "history",
    "versions.lock": "the generated list itself",
    "*.patch": "patch content",
    "config/edition/edits.tsv": "export edits: sha256 of our own test files",
    "config/edition/private.txt": "export scrub list: hashes that must never ship (#246), not pins",
    "config/edition/allow.tsv": "export allowances: path + sha256 of shipped files (our art), not version pins",
    "docs/history/*": "history map of old commits (also skipped as docs/)",
    "skills/collections/thumbnails.tsv": "ROM thumbnail data",
    "skills/collections/mame_hidden_names.tsv": "MAME data",
}


class PinError(Exception):
    pass


# What looks like a pin (coverage scan): sha256 values and digests, 40-hex commits, `pkg==version` requirement pins
# and Ansible collection versions. Group 1 is the value that must be explained.
PIN_LOOKS = [r"(?<![0-9a-f])([0-9a-f]{64})(?![0-9a-f])", r"(?<![0-9a-zA-Z])([0-9a-f]{40})(?![0-9a-zA-Z])"]
REQ_LOOKS = {"requirements": r"^[A-Za-z0-9_.-]+==([^\s;\\]+)", "requirements.yml": r'^\s+version: "([^"]+)"'}


def _looks(rel):
    rxs = list(PIN_LOOKS)
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(".yml") and "requirements" in name:
        rxs.append(REQ_LOOKS["requirements.yml"])
    elif "requirements" in name and name.endswith(".txt"):
        rxs.append(REQ_LOOKS["requirements"])
    return rxs


# What the scan reads: code, config, Dockerfiles and build files. Prose (docs/**, notes/**, every *.md, SKILL.md
# included) is skipped: a commit or sha quoted in a note is not a pin, and the scan cannot judge prose.
CODE_SUFFIXES = {".sh", ".py", ".yml", ".yaml", ".json", ".toml", ".j2", ".mk", ".cfg", ".conf", ".ini", ".service",
                 ".path", ".timer", ".rules", ".txt", ".tsv", ".xml", ".js", ".lua"}
CODE_NAMES = {"Dockerfile", "Makefile", "Containerfile"}


def scannable(rel, root=ROOT):
    """Code or config the coverage scan reads (prose and binaries are skipped)."""
    if rel.startswith(("docs/", "notes/")) or rel.endswith(".md"):
        return False
    name = rel.rsplit("/", 1)[-1]
    suffix = name[name.rfind("."):] if "." in name else ""
    if name in CODE_NAMES or suffix in CODE_SUFFIXES:
        return True
    if suffix:
        return False
    try:                                      # an extension-less script (bin/ans, files/coin-dojo-*): its #! line
        with open(pathlib.Path(root) / rel, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def not_pin(rel):
    import fnmatch
    return next((why for g, why in NOT_PINS.items() if fnmatch.fnmatch(rel, g)), None)


def explained_values(root=ROOT):
    """file -> set of values the components (every field, every location, also inactive ones) and OWN explain."""
    out = {}
    for c in COMPONENTS:
        for loc in c["locs"]:
            p = pathlib.Path(root) / loc["file"]
            if not p.exists():
                continue
            text = p.read_text()
            for rxs in loc["fields"].values():
                for rx in rxs:
                    for v, _, _ in (_matches(text, rx, loc["region"]) or []):
                        out.setdefault(loc["file"], set()).add(v)
        for f, rx in c["multi"].values():
            p = pathlib.Path(root) / f
            if p.exists():
                out.setdefault(f, set()).update(re.findall(rx, p.read_text()))
    for f, rxs in OWN.items():
        p = pathlib.Path(root) / f
        if p.exists():
            for rx in rxs:
                out.setdefault(f, set()).update(re.findall(rx, p.read_text(), re.M))
    return out


def unexplained(paths, root=ROOT):
    """-> ['file:line: value'] for every pin-looking value in `paths` (repo-relative) that no component, OWN or
    NOT_PINS entry explains: a new pin source, or a new pin in a known one, that tools/versions.py does not list."""
    known, bad = explained_values(root), []
    for rel in paths:
        if not_pin(rel) or not scannable(rel, root):
            continue
        try:
            text = (pathlib.Path(root) / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for rx in _looks(rel):
            for m in re.finditer(rx, text, re.M):
                if m.group(1) not in known.get(rel, set()):
                    bad.append("%s:%d: %s" % (rel, text.count("\n", 0, m.start()) + 1, m.group(1)))
    return bad


def read(rel, root=ROOT):
    return (pathlib.Path(root) / rel).read_text()


def _matches(text, rx, region):
    """-> list of (value, start, end) of group 1, inside the region when one is given."""
    spans = [(0, len(text))]
    if region:
        spans = [(m.start(), m.end()) for m in re.finditer(region, text, re.S)]
        if not spans:
            return None
    out = []
    for a, b in spans:
        for m in re.compile(rx, re.M).finditer(text, a, b):
            out.append((m.group(1), m.start(1), m.end(1)))
    return out


def active_locs(comp, root=ROOT):
    for loc in comp["locs"]:
        p = pathlib.Path(root) / loc["file"]
        if loc["unless_exists"] and (pathlib.Path(root) / loc["unless_exists"]).exists():
            continue
        if loc["optional"] and not p.exists():
            continue
        if loc["when"] and (not p.exists() or not re.search(loc["when"], p.read_text(), re.M)):
            continue
        yield loc


def extract(comp, root=ROOT, files=None):
    """-> (row dict, [(field, file, line)]). Raises PinError when a pin is missing or two places disagree.
    files: {path: text} read instead of the disk (bump validates its edits in memory before writing)."""
    vals, where = {}, []
    files = files or {}
    for loc in active_locs(comp, root):
        try:
            text = files[loc["file"]] if loc["file"] in files else read(loc["file"], root)
        except OSError:
            raise PinError("%s: pin source %s is missing" % (comp["name"], loc["file"]))
        for field, rxs in loc["fields"].items():
            for rx in rxs:
                ms = _matches(text, rx, loc["region"])
                if not ms:
                    raise PinError("%s: %s not found in %s (%s): the pin moved; fix tools/versions.py"
                                   % (comp["name"], field, loc["file"], loc["at"]))
                for v, s, _ in ms:
                    if field in vals and vals[field] != v:
                        raise PinError("%s: %s is %r in %s but %r elsewhere: every place must name the same pin "
                                       "(make bump changes all of them)" % (comp["name"], field, v, loc["file"], vals[field]))
                    vals[field] = v
                    where.append((field, loc["file"], text.count("\n", 0, s) + 1))
    for field, (f, rx) in comp["multi"].items():
        hs = sorted(set(re.findall(rx, files[f] if f in files else read(f, root))))
        vals[field] = "%d hashes, set %s" % (len(hs), hashlib.sha256(" ".join(hs).encode()).hexdigest()[:12])
    for f, why in comp.get("checks", []):
        bad = f(vals)
        if bad:
            raise PinError("%s: %s" % (comp["name"], bad))
    shas = sorted((k, v) for k, v in vals.items() if k.startswith("sha256:"))
    sha = vals.get("sha256") or " ".join("%s=%s" % (k[7:], v) for k, v in shas)
    commit = vals.get("commit", "")
    row = {"component": comp["name"], "version": vals.get("version") or (commit[:12] if commit else "-"),
           "commit": commit or "-", "sha256": sha or "-", "source": vals.get("source") or "-",
           "pinned_in": " ".join(sorted({"%s:%s" % (l["file"], l["at"]) for l in active_locs(comp, root)}))}
    return row, where


COLS = ("component", "version", "commit", "sha256", "source", "pinned_in")
HEADER = ("# versions.lock - every pinned third-party component (#278). GENERATED by `make versions` "
          "(tools/versions.py) from the pin sources; do not edit.\n"
          "# A change here needs a CHANGELOG `### Versions` line \"component old -> new\" (tools/changelog.py versions).\n"
          "# Change a pin with `make bump C=<component> V=<version> [SHA=...] [COMMIT=...]` (skills/versions/SKILL.md).\n")


def rows(root=ROOT):
    return [extract(c, root)[0] for c in COMPONENTS]


def render(rs):
    return HEADER + "\t".join(COLS) + "\n" + "".join("\t".join(r[c] for c in COLS) + "\n" for r in rs)


def parse_lock(text):
    """-> {component: row}. Comment lines and the header row are skipped."""
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#") or line.startswith("component\t"):
            continue
        parts = line.split("\t")
        if len(parts) == len(COLS):
            out[parts[0]] = dict(zip(COLS, parts))
    return out


def changes(old, new):
    """-> [(component, old_row|None, new_row|None)] where version, commit or sha256 differ (source-only and
    pinned_in-only changes need no CHANGELOG line)."""
    key = lambda r: (r["version"], r["commit"], r["sha256"]) if r else None
    out = []
    for name in sorted(set(old) | set(new)):
        if key(old.get(name)) != key(new.get(name)):
            out.append((name, old.get(name), new.get(name)))
    return out


def label(r):
    """How a version is written in CHANGELOG lines: the version, plus the short commit/sha when that is all that moved."""
    if r is None:
        return "(none)"
    sha = r["sha256"]
    fp = re.search(r"set ([0-9a-f]+)", sha)
    short = fp.group(1)[:7] if fp else sha.split()[0].split("=")[-1][:7]
    extra = r["commit"][:7] if r["commit"] != "-" else (short if sha != "-" else "")
    return r["version"] if not extra or extra in r["version"] else "%s (%s)" % (r["version"], extra)


def changelog_line(name, old, new):
    if old is None:
        return "- %s %s (new)" % (name, label(new))
    if new is None:
        return "- %s %s → removed" % (name, label(old))
    return "- %s %s → %s" % (name, label(old), label(new))


def by_name(name):
    for c in COMPONENTS:
        if c["name"] == name:
            return c
    raise PinError("unknown component %r (tools/versions.py show lists them)" % name)


def _edit(text, edits):
    """Apply (start, end, value) edits to text. Spans inside another edited span are dropped (that span already
    writes them); partly overlapping spans are refused. Applied from the end, so earlier offsets stay valid."""
    edits = sorted(set(edits))
    keep = []
    for s_, e_, v in edits:
        outer = [k for k in edits if k != (s_, e_, v) and k[0] <= s_ and e_ <= k[1] and (k[1] - k[0]) > (e_ - s_)]
        if not outer:
            keep.append((s_, e_, v))
    for (a1, b1, _), (a2, b2, _) in zip(keep, keep[1:]):
        if a2 < b1:
            raise PinError("two pin fields overlap at characters %d-%d and %d-%d: fix the regexes in tools/versions.py"
                           % (a1, b1, a2, b2))
    for s_, e_, v in reversed(keep):
        text = text[:s_] + v + text[e_:]
    return text


def bump(name, version, sha=None, commit=None, root=ROOT, url=None):
    """Rewrite every capture of the component's fields in every active location. Everything is edited and checked
    IN MEMORY first (the new pins extract cleanly and agree, version/commit/sha256 read back as given), and only
    then written: a refused bump leaves every file byte-identical. Returns (files changed, old row, new row).
    Refuses when a field the component pins was not given (a new version never keeps the old sha256/commit).
    url: the new source URL as a whole (needed where a version cannot be derived, e.g. Pi OS's two dates)."""
    comp = by_name(name)
    old, _ = extract(comp, root)
    fields = set()
    for loc in active_locs(comp, root):
        fields |= set(loc["fields"])
    if comp["multi"]:
        raise PinError("%s pins a hash list (%s): edit it by hand, then make versions" % (name, ", ".join(comp["multi"])))
    new = {}
    if "version" in fields:
        new["version"] = version
    elif "commit" in fields:
        commit = commit or version
    else:
        raise PinError("%s has no version to bump" % name)
    if "commit" in fields:
        if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise PinError("%s is pinned by commit: give COMMIT=<40 hex> too" % name)
        new["commit"] = commit
    tagged = sorted(f for f in fields if f.startswith("sha256"))
    if tagged:
        if not sha:
            raise PinError("%s is pinned by sha256 (%s): give SHA=... too" % (name, ", ".join(tagged)))
        if tagged == ["sha256"]:
            given = {"sha256": sha}
        else:
            given = {"sha256:" + k: v for k, v in (p.split("=", 1) for p in sha.split(",") if "=" in p)}
        missing = [t for t in tagged if t not in given]
        if missing:
            raise PinError("%s needs SHA=%s" % (name, ",".join("%s=<sha256>" % t[7:] for t in missing)))
        for t in tagged:
            if not re.fullmatch(r"[0-9a-f]{64}", given[t]):
                raise PinError("%s: %s is not a sha256 (64 hex)" % (name, t))
            new[t] = given[t]
    if url is not None:
        if "source" not in fields:
            raise PinError("%s has no source URL to set" % name)
        if not re.fullmatch(r"https://[^\s\"'$`]+", url):
            raise PinError("URL=%r is not a plain https URL" % url)
        new["source"] = url
    elif comp.get("needs_url"):
        raise PinError("%s: %s; give URL=<the new download URL> too" % (name, comp["needs_url"]))
    # Fields bump never sets (a date inside a URL, ...) come from the URL: they need url=.
    other = sorted(f for f in fields if f not in new and f != "source")
    if other and url is None:
        raise PinError("%s also pins %s, which bump cannot derive: give URL=" % (name, ", ".join(other)))
    texts = {}
    for loc in active_locs(comp, root):
        f = loc["file"]
        text = texts.get(f, read(f, root))
        edits = []
        for field, rxs in loc["fields"].items():
            if field in new:
                for rx in rxs:
                    edits += [(s_, e_, new[field]) for _, s_, e_ in _matches(text, rx, loc["region"])]
        texts[f] = _edit(text, edits)
    after, _ = extract(comp, root, texts)          # in memory: a bump that breaks or splits a pin stops here
    for f, v in new.items():
        if f.startswith("sha256:"):
            got = dict(p.split("=", 1) for p in after["sha256"].split())[f[7:]]
        else:
            got = after[f]
        if got != v:
            raise PinError("%s: after the bump %s would read %r, not %r: nothing written" % (name, f, got, v))
    if "version" in new and "source" not in new and old["source"] != "-" and old["version"] in old["source"]:
        left = re.search(r"(?<![0-9A-Za-z.])%s(?![0-9A-Za-z])" % re.escape(old["version"]), after["source"])
        if version not in after["source"] or (left and old["version"] != version):
            raise PinError("%s: the source URL would read %s (old %s left or new %s missing): nothing written"
                           % (name, after["source"], old["version"], version))
    changed = []
    for f, text in texts.items():
        p = pathlib.Path(root) / f
        if text != p.read_text():
            changed.append(f)
    for f in changed:
        (pathlib.Path(root) / f).write_text(texts[f])
    return changed, old, after


def add_changelog_line(line, root=ROOT):
    """Put the bullet under [Unreleased] -> ### Versions (made when missing). Idempotent."""
    p = pathlib.Path(root) / "CHANGELOG.md"
    text = p.read_text()
    lines = text.splitlines(keepends=True)
    i = next(n for n, l in enumerate(lines) if l.startswith("## [Unreleased]"))
    j = next((n for n in range(i + 1, len(lines)) if lines[n].startswith("## [") or re.match(r"^\[[^\]]+\]:", lines[n])),
             len(lines))
    if any(l.rstrip("\n") == line for l in lines[i:j]):
        return False
    k = next((n for n in range(i + 1, j) if lines[n].strip() == "### Versions"), None)
    if k is None:
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        lines[j:j] = ["\n", "### Versions\n", "\n", line + "\n"]
    else:
        m = k + 1
        while m < j and (not lines[m].strip() or lines[m].lstrip().startswith("- ")):
            m += 1
        while m > k + 1 and not lines[m - 1].strip():
            m -= 1
        lines[m:m] = [line + "\n"]
    p.write_text("".join(lines))
    return True


def main(argv):
    root = pathlib.Path(os.environ.get("VERSIONS_ROOT", ROOT))
    try:
        if argv[:1] == ["generate"]:
            text = render(rows(root))
            lock = root / LOCK
            if "--check" in argv[1:]:
                if not lock.exists() or lock.read_text() != text:
                    print("FAIL %s is stale: run make versions (and add the CHANGELOG ### Versions line)" % LOCK)
                    print("RESULT: FAIL versions: stale lock")
                    return 1
                print("RESULT: PASS versions: %s matches the pin sources (%d components)" % (LOCK, len(COMPONENTS)))
                return 0
            lock.write_text(text)
            print("RESULT: PASS versions: wrote %s (%d components)" % (LOCK, len(COMPONENTS)))
            return 0
        if argv[:1] == ["show"]:
            for c in COMPONENTS:
                r, where = extract(c, root)
                print("%-28s %-24s %s" % (r["component"], r["version"], r["what"] if "what" in r else c["what"]))
                for f, file, line in where:
                    print("    %-16s %s:%d" % (f, file, line))
            return 0
        if argv[:1] == ["changes"] and len(argv) == 2:
            old = parse_lock(pathlib.Path(argv[1]).read_text())
            for name, o, n in changes(old, parse_lock(render(rows(root)))):
                print(changelog_line(name, o, n))
            return 0
        if argv[:1] == ["bump"] and len(argv) >= 3:
            opts = dict(zip(argv[3::2], argv[4::2]))
            if set(opts) - {"--sha", "--commit", "--url"} or len(argv[3:]) % 2:
                raise PinError("usage: versions.py bump C V [--sha S] [--commit X] [--url U]")
            files, old, new = bump(argv[1], argv[2], opts.get("--sha") or None, opts.get("--commit") or None, root,
                                   opts.get("--url") or None)
            (root / LOCK).write_text(render(rows(root)))
            line = changelog_line(argv[1], old, new)
            added = add_changelog_line(line, root) if files else False
            print("versions: %s changed in %s" % (argv[1], ", ".join(files) or "nothing (already at that pin)"))
            print("versions: CHANGELOG %s: %s" % ("line added" if added else "unchanged", line))
            if files and by_name(argv[1]).get("note"):
                print("versions: TODO by hand: %s" % by_name(argv[1])["note"])
            print("RESULT: PASS versions bump %s -> %s" % (argv[1], argv[2]))
            return 0
    except PinError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        print("RESULT: FAIL versions: %s" % e)
        return 1
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
