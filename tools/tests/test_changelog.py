#!/usr/bin/env python3
"""Tests for tools/changelog.py (ticket #95): lint of CHANGELOG.md (Keep a Changelog 1.1.0) and check-tags.

Run: python3 tools/tests/test_changelog.py   (stdlib + git, throwaway repos)
The repo's own CHANGELOG.md must lint. Broken cases (must fail, naming the problem): unknown section "Misc", a version
without a link, out-of-order versions, a bad date, [Unreleased] missing/not first, bullets outside a section, an empty
released version; check-tags: a tag with no section, with an empty section, and a tag whose commit predates the section.
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

import os
os.environ["CHANGELOG_BASE"] = "http://forge.test:3000/alice/cab"   # #131: the compare-link base is config; tests pin it
ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "changelog.py"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, finish  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)
spec = importlib.util.spec_from_file_location("changelog", SCRIPT)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

HEAD = """# Changelog

[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html).

"""
GOOD = HEAD + """## [Unreleased]

### Added

- New thing (#1).

## [0.2.0] - 2026-11-20

### Fixed

- A fix.

## [0.1.0] - 2026-11-15

### Added

- First.

### Security

- Locked down.

[Unreleased]: http://x/compare/v0.2.0...HEAD
[0.2.0]: http://x/compare/v0.1.0...v0.2.0
[0.1.0]: http://x/releases/tag/v0.1.0
"""


def problems(text):
    return C.parse(text)[2]


def fails(msg, text, want):
    p = problems(text)
    check(any(want in x for x in p), "lint fails: %s (%s)" % (msg, p[:2]))


def test_lint():
    check(problems(GOOD) == [], "lint: a well-formed file passes (%s)" % problems(GOOD))
    r = subprocess.run([sys.executable, str(SCRIPT), "lint"], capture_output=True, text=True)
    check(r.returncode == 0 and "RESULT: PASS changelog lint" in r.stdout, "lint: the repo's CHANGELOG.md passes (%s)" % r.stdout.strip()[-120:])
    check(C.check_tags(ROOT) == [], "check-tags: this repo's own tags all have changelog sections (%s)" % C.check_tags(ROOT))
    fails('unknown section "Misc"', GOOD.replace("### Fixed", "### Misc"), "unknown section 'Misc'")
    fails("lowercase section", GOOD.replace("### Fixed", "### fixed"), "unknown section")
    fails("missing link", GOOD.replace("[0.2.0]: http://x/compare/v0.1.0...v0.2.0\n", ""), "missing link definition '[0.2.0]")
    fails("missing Unreleased link", GOOD.replace("[Unreleased]: http://x/compare/v0.2.0...HEAD\n", ""), "[Unreleased]")
    fails("out-of-order versions", GOOD.replace("0.2.0", "0.0.9", 2).replace("[0.0.9]: http://x/compare/v0.1.0...v0.0.9", "[0.0.9]: http://x/y"), "out of order")
    fails("equal versions", GOOD.replace("## [0.2.0]", "## [0.1.0]"), "out of order")
    fails("bad date (month 13)", GOOD.replace("2026-11-20", "2026-13-20"), "ISO date")
    fails("bad date format", GOOD.replace("2026-11-20", "20.11.2026"), "ISO date")
    fails("missing date", GOOD.replace(" - 2026-11-20", ""), "ISO date")
    fails("no Unreleased", GOOD.replace("## [Unreleased]", "## [0.3.0] - 2026-12-01").replace("[Unreleased]:", "[0.3.0]:"), "Unreleased")
    fails("Unreleased not first", GOOD.replace("## [Unreleased]\n\n### Added\n\n- New thing (#1).\n\n", "") + "\n## [Unreleased]\n", "Unreleased")
    fails("bullet outside a section", GOOD.replace("### Fixed\n\n", ""), "bullet outside")
    fails("empty released version", GOOD.replace("- A fix.\n", ""), "[0.2.0] has no entries")
    fails("section twice", GOOD.replace("### Security", "### Added"), "twice")
    fails("missing keepachangelog link", GOOD.replace("keepachangelog.com/en/1.1.0", "example.com"), "keepachangelog")
    fails("missing semver link", GOOD.replace("semver.org/spec/v2.0.0", "example.com"), "semver")
    fails("wrong title", GOOD.replace("# Changelog", "# Changes"), "first line")
    fails("link without heading", GOOD + "[0.9.9]: http://x\n", "no matching version")
    fails("non-semver version", GOOD.replace("0.2.0", "0.2"), "not X.Y.Z")
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "CHANGELOG.md"
        f.write_text(GOOD.replace("### Fixed", "### Misc"))
        r = subprocess.run([sys.executable, str(SCRIPT), "lint", str(f)], capture_output=True, text=True)
        check(r.returncode == 1 and "RESULT: FAIL changelog lint" in r.stdout, "lint: CLI exits 1 on a broken file")


def test_release_text():
    new, body = C.release_text(GOOD, "0.3.0", "2026-12-01")
    check(problems(new) == [], "release_text: result lints (%s)" % problems(new))
    check(new.index("## [0.3.0]") < new.index("## [0.2.0]") and "## [Unreleased]\n\n## [0.3.0] - 2026-12-01\n" in new, "release_text: new section on top, empty Unreleased")
    check("[Unreleased]: http://forge.test:3000/alice/cab/compare/v0.3.0...HEAD" in new and
          "[0.3.0]: http://forge.test:3000/alice/cab/compare/v0.2.0...v0.3.0" in new and "[0.1.0]: http://x/releases/tag/v0.1.0" in new,
          "release_text: links updated, old links kept")
    check("New thing" in body and "Fixed" not in body, "release_text: body is the Unreleased content only")
    try:
        C.release_text(new, "0.4.0", "2026-12-02")
        check(False, "release_text: refuses an empty Unreleased")
    except ValueError:
        check(True, "release_text: refuses an empty Unreleased")


def git(repo, *a):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *a], capture_output=True, text=True)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout.strip()


def test_check_tags():
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp).resolve() / "r"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        cl = repo / "CHANGELOG.md"
        git(repo, "commit", "-q", "--allow-empty", "-m", "root")
        git(repo, "tag", "v0.0.1")                                   # predates any changelog
        check(any("v0.0.1" in x for x in C.check_tags(repo)), "check-tags: a tag with no CHANGELOG.md at all names the tag")
        git(repo, "tag", "-d", "v0.0.1")
        check(C.check_tags(repo) == [], "check-tags: no tags is fine")
        cl.write_text(GOOD)
        git(repo, "add", "CHANGELOG.md")
        git(repo, "commit", "-q", "-m", "changelog with 0.1.0 and 0.2.0")
        git(repo, "tag", "-a", "v0.2.0", "-m", "x")
        git(repo, "tag", "v0.1.0")
        git(repo, "tag", "v0.3.0-rc1")                               # not a release tag: ignored
        check(C.check_tags(repo) == [], "check-tags: tags with sections pass (%s)" % C.check_tags(repo))
        r = subprocess.run([sys.executable, str(SCRIPT), "check-tags", str(repo)], capture_output=True, text=True)
        check(r.returncode == 0 and "RESULT: PASS" in r.stdout, "check-tags: CLI passes")
        # a tag whose commit predates its section
        git(repo, "tag", "v0.5.0")
        cl.write_text(GOOD.replace("## [0.2.0]", "## [0.5.0] - 2026-12-01\n\n### Fixed\n\n- later\n\n## [0.2.0]", 1)
                      .replace("[0.2.0]:", "[0.5.0]: http://x/c\n[0.2.0]:", 1))
        p = C.check_tags(repo)
        check(len(p) == 1 and "v0.5.0" in p[0] and "tagged commit" in p[0], "check-tags: section added after the tag -> names v0.5.0 (%s)" % p)
        git(repo, "tag", "-d", "v0.5.0")
        # tag with no section in the file
        git(repo, "tag", "v0.9.0")
        p = C.check_tags(repo)
        check(any(x.startswith("v0.9.0") for x in p), "check-tags: tag with no section fails naming it (%s)" % p)
        r = subprocess.run([sys.executable, str(SCRIPT), "check-tags", str(repo)], capture_output=True, text=True)
        check(r.returncode == 1 and "FAIL v0.9.0" in r.stdout, "check-tags: CLI exits 1")
        git(repo, "tag", "-d", "v0.9.0")
        # tag with an empty section (heading only)
        empty = GOOD.replace("- A fix.\n", "")
        cl.write_text(empty)
        git(repo, "commit", "-qam", "empty 0.2.0")
        p = C.check_tags(repo)
        check(any(x.startswith("v0.2.0") and "empty" in x for x in p), "check-tags: tag with an empty section fails naming it (%s)" % p)


def main():
    test_lint()
    test_release_text()
    test_check_tags()
    return finish()


if __name__ == "__main__":
    sys.exit(main())
