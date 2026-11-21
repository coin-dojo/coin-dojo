#!/usr/bin/env python3
"""#278: versions.lock - every pin listed, the committed lock fresh, a lock change named in the CHANGELOG, bumps whole.

Runs tools/versions.py and tools/changelog.py on the repo (read only) and on copies of the pin sources in a temp dir.
Broken cases that must FAIL: a stale lock; a new pin source no component lists (a planted file, and a new pin in a
known file); a lock change without its CHANGELOG `### Versions` line (also: no arrow, a removal not called removed);
a hand edit that changes one place of a pin and not the others (and a bump that would leave that); a bump missing
the COMMIT/SHA its component pins.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "lib"))
sys.path.insert(0, str(ROOT / "tools"))
from hktest import check, main  # noqa: E402
import changelog as CL  # noqa: E402
import versions as V  # noqa: E402

TOOL = ROOT / "tools" / "versions.py"
NEW_COMMIT = "0123456789abcdef0123456789abcdef01234567"
NEW_SHA = "ab" * 32
FIXTURE_CL = ("# Changelog\n\nkeepachangelog.com/en/1.1.0 semver.org/spec/v2.0.0\n\n## [Unreleased]\n\n### Added\n\n- x\n\n"
              "### Versions\n\n- ruff 0.17.5 (new)\n\n## [0.1.0] - 2026-11-01\n\n### Added\n\n- y\n\n"
              "[Unreleased]: u\n[0.1.0]: v\n")


def tracked():
    return subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout.split()


def copy_sources(t):
    """A temp root with every pin source, the OWN files, CHANGELOG.md and versions.lock (same paths)."""
    files = {loc["file"] for c in V.COMPONENTS for loc in c["locs"]} | {f for c in V.COMPONENTS for f, _ in c["multi"].values()}
    files |= set(V.OWN) | {"versions.lock"}
    for f in files:
        src = ROOT / f
        if src.exists():
            dst = pathlib.Path(t) / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (pathlib.Path(t) / "CHANGELOG.md").write_text(FIXTURE_CL)   # not the repo's: the community export empties it
    return pathlib.Path(t)


def run(*args, root=None):
    env = dict(os.environ, **({"VERSIONS_ROOT": str(root)} if root else {}))
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=env)


def edit(root, rel, old, new):
    p = root / rel
    text = p.read_text()
    assert old in text, (rel, old)
    p.write_text(text.replace(old, new, 1))


def test_committed_lock_is_fresh():
    r = run("generate", "--check")
    check(r.returncode == 0, "versions.lock matches the pin sources (%s)" % (r.stdout + r.stderr).strip()[-200:].replace("\n", " | "))
    lock = V.parse_lock((ROOT / "versions.lock").read_text())
    check(len(lock) == len(V.COMPONENTS) >= 30, "one row per component (%d)" % len(lock))
    for name in ("retroarch", "mupen64plus-next", "stella", "flycast", "box64", "wine", "electron", "fightcade",
                 "raspios-lite-arm64", "es-de", "debian-trixie-build", "python-ansible-image"):
        check(name in lock, "versions.lock lists %s" % name)
    check(lock["retroarch"]["version"].startswith("v") and len(lock["retroarch"]["commit"]) == 40, "retroarch has its tag and commit")


def test_stale_lock_fails():
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        check(run("generate", "--check", root=root).returncode == 0, "the copy starts fresh")
        edit(root, "ansible/requirements.txt", "ruff==", "ruff==9")
        r = run("generate", "--check", root=root)
        check(r.returncode == 1 and "stale" in r.stdout, "BROKEN: a pin changed without make versions -> stale lock FAILs")
        check(run("generate", root=root).returncode == 0 and run("generate", "--check", root=root).returncode == 0,
              "make versions fixes it")


def test_every_pin_is_listed():
    bad = V.unexplained(tracked())
    check(bad == [], "every pin-looking value in a tracked file is a listed component, OWN or NOT_PINS: %s" % bad[:5])
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        (root / "build" / "newemu").mkdir(parents=True)
        (root / "build/newemu/build.sh").write_text('NEW_COMMIT="%s"\nBASE_IMAGE="x@sha256:%s"\n' % (NEW_COMMIT, NEW_SHA))
        bad = V.unexplained(["build/newemu/build.sh"], root)
        check(len(bad) == 2 and all(b.startswith("build/newemu/build.sh:") for b in bad),
              "BROKEN: an unlisted pin source (a new build.sh with a commit and a digest) is reported: %s" % bad)
        edit(root, "build/retroarch/build.sh", 'RA_REPO=', 'EXTRA_SHA256="%s"\nRA_REPO=' % NEW_SHA)
        bad = V.unexplained(["build/retroarch/build.sh"], root)
        check(bad and NEW_SHA in bad[0], "BROKEN: a new pin in a known source that no field captures is reported: %s" % bad)
        edit(root, "ansible/requirements.txt", "ruff==", "black==24.1.0\nruff==")
        check(any("24.1.0" in b for b in V.unexplained(["ansible/requirements.txt"], root)),
              "BROKEN: a new == requirement pin is reported")


def test_places_must_agree():
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        old = V.parse_lock((root / "versions.lock").read_text())["debian-trixie-build"]["sha256"]
        edit(root, "build/n64/build.sh", old, NEW_SHA)          # a hand bump of one of the three build images
        r = run("generate", root=root)
        check(r.returncode == 1 and "debian-trixie-build" in r.stdout and "every place must name the same pin" in r.stdout,
              "BROKEN: one place of a pin edited, the others not -> generation stops, names the component")
        r = run("bump", "debian-trixie-build", "trixie", "--sha", NEW_SHA, root=root)
        check(r.returncode == 1, "BROKEN: bump refuses on a pin whose places already disagree")


def test_bump_changes_every_place_lock_and_changelog():
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        before = (root / "build/retroarch/build.sh").read_text()
        r = run("bump", "retroarch", "v1.22.3", root=root)
        check(r.returncode == 1 and "COMMIT" in r.stdout and (root / "build/retroarch/build.sh").read_text() == before,
              "BROKEN: a commit-pinned component without COMMIT= is refused, nothing written")
        r = run("bump", "retroarch", "v1.22.3", "--commit", NEW_COMMIT, root=root)
        text = (root / "build/retroarch/build.sh").read_text()
        check(r.returncode == 0 and 'RA_TAG="v1.22.3"' in text and 'RA_COMMIT="%s"' % NEW_COMMIT in text,
              "bump retroarch: tag and commit rewritten (%s)" % r.stdout.strip()[-160:].replace("\n", " | "))
        check(run("generate", "--check", root=root).returncode == 0, "the lock is regenerated (fresh after the bump)")
        cl = (root / "CHANGELOG.md").read_text()
        old_ra = V.parse_lock((ROOT / "versions.lock").read_text())["retroarch"]
        line = "- retroarch %s → v1.22.3 (0123456)" % V.label(old_ra)
        check(line in cl and cl.index("### Versions") < cl.index(line) < cl.index("## [0."),
              "the CHANGELOG line is under [Unreleased] ### Versions")
        check(CL.parse(cl)[2] == [], "the CHANGELOG still lints")
        check(CL.versions_problems(cl, (ROOT / "versions.lock").read_text(), (root / "versions.lock").read_text()) == [],
              "and satisfies the versions check against the old lock")
    with tempfile.TemporaryDirectory() as t:      # sha-pinned in two places: both change, SHA required
        root = copy_sources(t)
        r = run("bump", "es-de", "3.6.0", root=root)
        check(r.returncode == 1 and "SHA=" in r.stdout, "BROKEN: a sha-pinned component without SHA= is refused")
        r = run("bump", "es-de", "3.6.0", "--sha", NEW_SHA, root=root)
        cs = (root / "skills/build-image/scripts/chroot-setup.sh").read_text()
        vm = (root / "ansible/roles/vm_base/defaults/main.yml").read_text()
        check(r.returncode == 0 and "release/3.6.0/" in cs and "release/3.6.0/" in vm and NEW_SHA in vm and "3.5.0" not in cs + vm,
              "bump es-de: the image build's URL and the VM role's URL + sha256 all change")
        check(run("generate", "--check", root=root).returncode == 0, "lock fresh after the es-de bump")
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        check(run("bump", "pyyaml-config", "6.0.4", root=root).returncode == 1, "a hash-list pin is refused (edit by hand)")
        check(run("bump", "nope", "1", root=root).returncode == 1, "an unknown component is refused")


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(pathlib.Path(root).rglob("*")) if p.is_file()}


def pin_lines(root, rel, *keys):
    return [l for l in (root / rel).read_text().splitlines() if any(k in l for k in keys)]


PI_URL = ("https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-%s/"
          "%s-raspios-trixie-arm64-lite.img.xz")


def test_bump_to_longer_and_shorter_versions():
    """Review MUST: a version inside a URL was rewritten from stale offsets (es-de 3.10.0 -> `...AppImagee`)."""
    cases = [  # component, new versions (longer, shorter), files, the exact lines they must end up as
        ("es-de", ("3.10.0", "4.0"), lambda v: {
            "skills/build-image/scripts/chroot-setup.sh": ["  curl -fL --retry 3 -o /opt/ES-DE/ES-DE_aarch64.AppImage "
                                                           "https://packages.es-de.org/release/%s/ES-DE_aarch64.AppImage" % v],
            "ansible/roles/vm_base/defaults/main.yml": ["vm_base_esde_url: https://packages.es-de.org/release/%s/ES-DE_aarch64.AppImage" % v]}),
        ("wine", ("10.12", "9"), lambda v: {   # #267: build/fightcade/pins.json is the one source
            "build/fightcade/pins.json": ['    "title": "Wine %s",' % v,
                                          '    "url": "https://github.com/Kron4ek/Wine-Builds/releases/download/%s/wine-%s-amd64-wow64.tar.xz",' % (v, v),
                                          '    "file": "wine-%s-amd64-wow64.tar.xz",' % v, '    "sha256": "%s",' % NEW_SHA]}),
        ("electron", ("12.10.11", "9.2"), lambda v: {
            "build/fightcade/pins.json": ['    "title": "Electron %s",' % v,
                                          '    "url": "https://github.com/electron/electron/releases/download/v%s/electron-v%s-linux-arm64.zip",' % (v, v),
                                          '    "file": "electron-v%s-linux-arm64.zip",' % v,
                                          '    "shasums_url": "https://github.com/electron/electron/releases/download/v%s/SHASUMS256.txt"' % v]}),
    ]
    for comp, versions, want in cases:
        for v in versions:
            with tempfile.TemporaryDirectory() as t:
                root = copy_sources(t)
                r = run("bump", comp, v, "--sha", NEW_SHA, root=root)
                ok = r.returncode == 0
                for rel, lines in want(v).items():
                    text = (root / rel).read_text().splitlines()
                    ok = ok and all(l in text for l in lines)
                check(ok and run("generate", "--check", root=root).returncode == 0,
                      "bump %s -> %s (%s): every URL and file name exact, lock fresh (%s)"
                      % (comp, v, "longer" if len(v) > 4 else "shorter", r.stdout.strip()[-120:].replace("\n", " | ")))
    for folder, fdate in (("2026-12-01", "2026-11-24"), ("2027-1-2", "2027-01-02")):
        with tempfile.TemporaryDirectory() as t:
            root = copy_sources(t)
            r = run("bump", "raspios-lite-arm64", folder, "--sha", NEW_SHA, "--url", PI_URL % (folder, fdate), root=root)
            got = pin_lines(root, "skills/build-image/scripts/base-image.sh", "PIN_URL=", "PIN_SHA256=")
            check(r.returncode == 0 and got == ["PIN_URL=" + PI_URL % (folder, fdate), "PIN_SHA256=" + NEW_SHA]
                  if folder == "2026-12-01" else r.returncode == 1 and "YYYY-MM-DD" in r.stdout,
                  "raspios %s/%s: the folder date and a different file date both land; a bad date is refused" % (folder, fdate))


def test_failed_bump_leaves_files_byte_identical():
    refusals = [
        (("raspios-lite-arm64", "2026-12-01", "--sha", NEW_SHA), "URL="),            # the two dates: no guess
        (("raspios-lite-arm64", "2026-12-02", "--sha", NEW_SHA, "--url", PI_URL % ("2026-12-01", "2026-12-01")),
         "would read"),                                                                # URL folder != V
        (("raspios-lite-arm64", "2026-12-01", "--sha", NEW_SHA, "--url", "https://example.invalid/x.img.xz"),
         "not found"),                                                                 # a URL of another shape
        (("es-de", "3.6.0"), "SHA="),
        (("es-de", "3.6.0", "--sha", "xyz"), "64 hex"),
        (("retroarch", "v1.22.3"), "COMMIT="),
        (("es-de", "3.6.0", "--sha", NEW_SHA, "--url", "http://insecure.example/x"), "https"),
    ]
    for args, why in refusals:
        with tempfile.TemporaryDirectory() as t:
            root = copy_sources(t)
            before = snapshot(root)
            r = run("bump", *args, root=root)
            check(r.returncode == 1 and why in r.stdout and snapshot(root) == before,
                  "BROKEN: bump %s refused (%s) and every file is byte-identical" % (" ".join(args[:2]), why))
    with tempfile.TemporaryDirectory() as t:       # a pin regex that would split a value: refused in memory
        root = copy_sources(t)
        before = snapshot(root)
        try:
            V._edit("abcdef", [(1, 4, "X"), (3, 5, "Y")])
            check(False, "BROKEN: partly overlapping edits are refused")
        except V.PinError:
            check(snapshot(root) == before, "BROKEN: partly overlapping edits are refused, nothing written")
        check(V._edit("aXbYc", [(0, 5, "ZZ"), (1, 2, "Q")]) == "ZZ", "an edit inside an edited span is dropped (no overlap)")


def test_bump_through_pins_json():
    """#267: Fightcade's pins live in build/fightcade/pins.json; a bump edits title, url, file, shasums_url, sha256."""
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        e = V.parse_lock((root / "versions.lock").read_text())["electron"]
        r = run("bump", "electron", "12.0.1", "--sha", NEW_SHA, root=root)
        js = (root / "build/fightcade/pins.json").read_text()
        check(r.returncode == 0 and js.count("12.0.1") == 5 and "v%s/" % e["version"] not in js and NEW_SHA in js
              and run("generate", "--check", root=root).returncode == 0,
              "bump electron in pins.json: title, url, file, shasums_url and sha256, lock fresh (%s)" % r.stdout.strip()[-100:].replace("\n", " | "))
        check("TODO by hand" in r.stdout and "size" in r.stdout, "the bump says the size is left to set by hand")
        check(all(l["file"] != "build/fightcade/fetch.sh" for c in V.COMPONENTS for l in c["locs"]),
              "fetch.sh is no pin source any more (it reads pins.json)")


def test_licence_manifest_follows_the_pin():
    """#254: the licence manifest's ref (and the commit in raw licence URLs) is one more place of a build pin."""
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        ra = V.parse_lock((root / "versions.lock").read_text())["retroarch"]["commit"]
        edit(root, V.MANIFEST, '"ref": "%s"' % ra, '"ref": "%s"' % NEW_COMMIT)
        r = run("generate", root=root)
        check(r.returncode == 1 and "retroarch" in r.stdout and "every place must name the same pin" in r.stdout,
              "BROKEN: a manifest ref that disagrees with RA_COMMIT stops generation")
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        r = run("bump", "retroarch", "v1.22.3", "--commit", NEW_COMMIT, root=root)
        man = (root / V.MANIFEST).read_text()
        check(r.returncode == 0 and man.count(NEW_COMMIT) == 2 and "licences.py" in r.stdout
              and run("generate", "--check", root=root).returncode == 0,
              "bump retroarch moves the manifest ref and licence URL too, and says to re-read the licence")


def test_pinned_cores_are_found_generically():
    """#274 adds build/<core>/build.sh (CORE=, CORE_COMMIT=) and a manifest entry per core: found by glob."""
    core = ('CORE="snes9x"\nCORE_COMMIT="%s"\nCORE_REPO="https://example.invalid/snes9x.git"\n')
    entry = ('{\n  "components": [\n    {\n      "id": "snes9x",\n      "origin": "built",\n      "build": "build/snes9x",\n'
             '      "ref": "%s",\n      "licence_files": [\n        {"name": "LICENSE", "url": "https://raw.githubusercontent.com/'
             'libretro/snes9x/%s/LICENSE", "sha256": "%s"}\n      ]\n    }\n  ]\n}\n')
    with tempfile.TemporaryDirectory() as t:
        root = pathlib.Path(t)
        (root / "build/snes9x").mkdir(parents=True)
        (root / "build/snes9x/build.sh").write_text(core % NEW_COMMIT)
        (root / "build/other").mkdir(parents=True)
        (root / "build/other/build.sh").write_text('OTHER_COMMIT="%s"\n' % NEW_COMMIT)
        (root / V.MANIFEST).parent.mkdir(parents=True)
        (root / V.MANIFEST).write_text(entry % (NEW_COMMIT, NEW_COMMIT, NEW_SHA))
        comps = V.core_components(root)
        check([c["name"] for c in comps] == ["snes9x"], "a build.sh with CORE= and CORE_COMMIT= is a component, others not")
        row, _ = V.extract(comps[0], root)
        check(row["commit"] == NEW_COMMIT and row["version"] == NEW_COMMIT[:12] and "example.invalid" in row["source"],
              "its commit, short version and repo come from the build.sh")
        (root / V.MANIFEST).write_text(entry % ("1" * 40, NEW_COMMIT, NEW_SHA))
        try:
            V.extract(comps[0], root)
            check(False, "BROKEN: a manifest ref that disagrees with CORE_COMMIT stops generation")
        except V.PinError as e:
            check("snes9x" in str(e), "BROKEN: a manifest ref that disagrees with CORE_COMMIT stops generation")


def test_when_skips_a_pin_not_there_yet():
    loc = V.L("x.sh", "GLSL_COMMIT", when=r'^GLSL_COMMIT="', commit=r'^GLSL_COMMIT="' + V.H40 + '"')
    comp = V.C("x", "x", loc)
    with tempfile.TemporaryDirectory() as t:
        root = pathlib.Path(t)
        (root / "x.sh").write_text("echo no pin yet\n")
        check(list(V.active_locs(comp, root)) == [], "a `when` location is skipped while its pin is absent")
        (root / "x.sh").write_text('GLSL_COMMIT="%s"\n' % NEW_COMMIT)
        check(V.extract(comp, root)[0]["commit"] == NEW_COMMIT, "and read once the pin is there")


def test_scan_reads_code_not_prose():
    with tempfile.TemporaryDirectory() as t:
        root = copy_sources(t)
        for rel in ("docs/reference/x.md", "notes/2026-11-20-x.md", "skills/foo/SKILL.md", "README.md", "build/x/README.md"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("commit `%s`, sha %s\n" % (NEW_COMMIT, NEW_SHA))
        (root / "skills/foo/bin").mkdir(parents=True)
        (root / "skills/foo/bin/tool").write_text("#!/bin/sh\nPIN=%s\n" % NEW_SHA)
        (root / "skills/foo/Dockerfile").write_text("FROM x@sha256:%s\n" % NEW_SHA)
        (root / "skills/foo/data.png").write_bytes(b"\x89PNG" + NEW_SHA.encode())
        bad = V.unexplained(["docs/reference/x.md", "notes/2026-11-20-x.md", "skills/foo/SKILL.md", "README.md",
                             "build/x/README.md", "skills/foo/bin/tool", "skills/foo/Dockerfile", "skills/foo/data.png"], root)
        check(sorted(b.split(":")[0] for b in bad) == ["skills/foo/Dockerfile", "skills/foo/bin/tool"],
              "prose (docs/, notes/, *.md) and binaries are skipped; a Dockerfile and a #! script are scanned: %s" % bad)


def test_changelog_versions_released_and_merge_base():
    old = (ROOT / "versions.lock").read_text()
    rows = V.parse_lock(old)
    r = dict(rows["ruff"], version="0.18.0")
    new = old.replace("\t".join(rows["ruff"][c] for c in V.COLS), "\t".join(r[c] for c in V.COLS))
    released = ("# Changelog\n\nkeepachangelog.com/en/1.1.0 semver.org/spec/v2.0.0\n\n## [Unreleased]\n\n### Added\n\n- x\n\n"
                "## [0.2.0] - 2026-11-30\n\n### Versions\n\n- ruff 0.17.5 → 0.18.0\n\n[Unreleased]: u\n[0.2.0]: v\n")
    check(CL.versions_problems(released, old, new) == [], "a change already in a released ### Versions is not asked again")
    check(len(CL.versions_problems(released.replace("0.18.0\n", "0.18.1\n"), old, new)) == 1,
          "BROKEN: a released line for another version does not count")
    with tempfile.TemporaryDirectory() as t:
        g = lambda *a: subprocess.run(["git", "-C", t, *a], capture_output=True, text=True, check=True,
                                      env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                                               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
                                               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"))
        g("init", "-q", "-b", "main")
        (pathlib.Path(t) / "versions.lock").write_text(old)
        g("add", "-A")
        g("commit", "-q", "-m", "base")
        g("update-ref", "refs/remotes/origin/main", "HEAD")
        lock, why = CL.base_lock(t)
        check(lock is None and "merge base" in why, "HEAD == origin/main: skipped with a reason (%s)" % why)
        (pathlib.Path(t) / "versions.lock").write_text(new)
        g("commit", "-q", "-am", "bump")
        lock, why = CL.base_lock(t)
        check(lock == old and why == "", "a branch commit: compared with the merge base's lock")
        env = dict(os.environ, CHANGELOG_REPO=t)
        (pathlib.Path(t) / "CHANGELOG.md").write_text(released.replace("0.18.0\n", "0.18.1\n"))
        r = subprocess.run([sys.executable, str(ROOT / "tools/changelog.py"), "versions"], capture_output=True, text=True, env=env)
        check(r.returncode == 1 and "ruff" in r.stdout, "BROKEN: the CLI fails on the missing line")
        g("update-ref", "refs/remotes/origin/main", "HEAD")
        r = subprocess.run([sys.executable, str(ROOT / "tools/changelog.py"), "versions"], capture_output=True, text=True, env=env)
        check(r.returncode == 0 and "SKIP" in r.stdout, "on origin/main itself the CLI skips with a clear message")


def test_changelog_versions_rule():
    old = (ROOT / "versions.lock").read_text()
    rows = V.parse_lock(old)
    r = dict(rows["ruff"], version="0.18.0")
    new = old.replace("\t".join(rows["ruff"][c] for c in V.COLS), "\t".join(r[c] for c in V.COLS))
    gone = "".join(l + "\n" for l in old.splitlines() if not l.startswith("ruff\t"))
    base = "# Changelog\n\n[keepachangelog.com/en/1.1.0] [semver.org/spec/v2.0.0]\n\n## [Unreleased]\n\n%s\n[Unreleased]: x\n"
    added = base % "### Added\n\n- x\n"
    check(len(CL.versions_problems(added, old, new)) == 1, "BROKEN: a lock change without a ### Versions line FAILs")
    check(CL.versions_problems(base % "### Versions\n\n- ruff 0.17.5 → 0.18.0\n", old, new) == [], "with the line it passes")
    check(CL.versions_problems(base % "### Versions\n\n- ruff 0.17.5 -> 0.18.0\n", old, new) == [], "ASCII -> is fine too")
    check(len(CL.versions_problems(base % "### Versions\n\n- ruff 0.18.0\n", old, new)) == 1,
          "BROKEN: a line without old -> new FAILs")
    check(len(CL.versions_problems(base % "### Fixed\n\n- ruff 0.17.5 → 0.18.0\n", old, new)) == 1,
          "BROKEN: the line under another section does not count")
    check(len(CL.versions_problems(base % "### Versions\n\n- ruffx 0.17.5 → 0.18.0\n", old, new)) == 1,
          "BROKEN: another component's name does not count")
    check(len(CL.versions_problems(added, old, gone)) == 1 and
          CL.versions_problems(base % "### Versions\n\n- ruff 0.17.5 → removed\n", old, gone) == [], "a removal needs 'removed'")
    check(CL.versions_problems(added, old, old) == [], "no lock change: nothing required")
    check(CL.versions_problems(added, old, old.replace("build/flycast/build.sh:FLYCAST_TAG", "build/flycast/build.sh:X")) == [],
          "a pinned_in-only change needs no line")


def test_repo_changelog_names_every_change():
    old, why = CL.base_lock(ROOT)
    if old is None:
        from hktest import skip
        skip("the CHANGELOG versions check is skipped: %s" % why)
        return
    probs = CL.versions_problems((ROOT / "CHANGELOG.md").read_text(), old, (ROOT / "versions.lock").read_text())
    check(probs == [], "every pin this branch changed has its CHANGELOG ### Versions line: %s" % probs[:3])


def test_release_section_lists_the_full_table():
    text = FIXTURE_CL
    rows = list(V.parse_lock((ROOT / "versions.lock").read_text()).values())
    os.environ.setdefault("CHANGELOG_BASE", "https://example.invalid/x/y")
    new, body = CL.release_text(text, "9.9.9", "2026-12-01", rows)
    ra = next(r for r in rows if r["component"] == "retroarch")
    check("| Component | Version | Commit / sha256 |" in body and "| retroarch | %s | %s |" % (ra["version"], ra["commit"][:12]) in body,
          "the release section's ### Versions ends with the full table")
    check(body.splitlines().count("### Versions") == 1 and CL.parse(new)[2] == [], "one Versions section, the new CHANGELOG lints")
    small = "# Changelog\n\nkeepachangelog.com/en/1.1.0 semver.org/spec/v2.0.0\n\n## [Unreleased]\n\n### Added\n\n- x\n\n[Unreleased]: u\n"
    _, b2 = CL.release_text(small, "0.1.0", "2026-12-01", rows)
    check("### Versions" in b2 and "| flycast |" in b2, "a release without version changes still gets the table")


if __name__ == "__main__":
    main(globals())
