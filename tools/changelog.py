#!/usr/bin/env python3
"""changelog (ticket #95): lint and edit CHANGELOG.md (Keep a Changelog 1.1.0 + SemVer 2.0.0).

  tools/changelog.py lint [FILE]                 exit 1 and print every problem; `make test` runs this on the repo's file
  tools/changelog.py check-tags [REPO]           every vX.Y.Z tag has a '## [X.Y.Z] - date' section with entries, both in
                                                 CHANGELOG.md now and in the tag's own commit (`git show vX.Y.Z:CHANGELOG.md`)
  tools/changelog.py unreleased [FILE]           print the body of [Unreleased] (empty output = nothing to release)
  tools/changelog.py versions [BASE]             #278: every component whose pin differs between BASE's versions.lock
                                                 (default: the merge base with origin/main) and the working one needs a
                                                 bullet under [Unreleased] `### Versions` naming it and its new version
                                                 ("component old → new"; a new one "component version (new)")
Library use (tools/release.py): parse(), unreleased_body(), release_text().
Format rules: first line `# Changelog`; links to keepachangelog.com/en/1.1.0 and semver.org/spec/v2.0.0 in the header;
`## [Unreleased]` first, then `## [X.Y.Z] - YYYY-MM-DD` newest first (strictly descending, real ISO dates); `### ` headings
only Added, Changed, Deprecated, Removed, Fixed, Security, Versions (#278: pinned component versions) (once per version); bullets only under a `### `; a released
version has at least one bullet; every version and [Unreleased] has a `[label]: url` link at the bottom.
"""
import datetime
import os
import pathlib
import re
import subprocess
import sys

SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security", "Versions")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hkconfig  # noqa: E402  (tools/hkconfig.py; the script folder is added by hand because callers may use -I)
VER = r"(\d+)\.(\d+)\.(\d+)"
H_VER = re.compile(r"^## \[(.+?)\](?: - (\S+))?(?: \[YANKED\])?\s*$")
LINK = re.compile(r"^\[([^\]]+)\]:\s*(\S+)\s*$")


def parse(text):
    """-> (versions, links, problems). versions: list of dicts in file order (Unreleased first when present)."""
    problems, versions, links = [], [], {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "# Changelog":
        problems.append("first line must be '# Changelog'")
    head = "\n".join(lines[:next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))])
    if "keepachangelog.com/en/1.1.0" not in head:
        problems.append("header must link https://keepachangelog.com/en/1.1.0/")
    if "semver.org/spec/v2.0.0" not in head:
        problems.append("header must link https://semver.org/spec/v2.0.0.html")
    cur = sec = None
    for n, l in enumerate(lines, 1):
        m = LINK.match(l)
        if m:
            links[m.group(1)] = m.group(2)
            cur = sec = None
            continue
        if l.startswith("## "):
            m = H_VER.match(l)
            sec = None
            if not m:
                problems.append("line %d: bad version heading %r (want '## [X.Y.Z] - YYYY-MM-DD')" % (n, l))
                cur = None
                continue
            name, date = m.groups()
            cur = {"name": name, "date": date, "bullets": 0, "sections": [], "line": n}
            versions.append(cur)
            if name == "Unreleased":
                if date:
                    problems.append("line %d: [Unreleased] takes no date" % n)
            else:
                if not re.fullmatch(VER, name):
                    problems.append("line %d: version %r is not X.Y.Z" % (n, name))
                try:
                    if not date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                        raise ValueError
                    datetime.date.fromisoformat(date)
                except ValueError:
                    problems.append("line %d: [%s] needs an ISO date YYYY-MM-DD, got %r" % (n, name, date))
        elif l.startswith("### "):
            title = l[4:].strip()
            if cur is None:
                problems.append("line %d: '### %s' outside a version" % (n, title))
                continue
            if title not in SECTIONS:
                problems.append("line %d: unknown section '%s' (allowed: %s)" % (n, title, ", ".join(SECTIONS)))
            if title in cur["sections"]:
                problems.append("line %d: section '%s' twice in [%s]" % (n, title, cur["name"]))
            cur["sections"].append(title)
            sec = title
        elif re.match(r"^\s*[-*] ", l):
            if cur is None or sec is None:
                problems.append("line %d: bullet outside a '### ' section" % n)
            else:
                cur["bullets"] += 1
        elif l.strip() and cur is not None and sec is None and not l.startswith("#"):
            problems.append("line %d: text under [%s] must sit in a '### ' section" % (n, cur["name"]))
    names = [v["name"] for v in versions]
    if not names or names[0] != "Unreleased" or names.count("Unreleased") != 1:
        problems.append("'## [Unreleased]' must be the first version heading, exactly once")
    rel = [v for v in versions if re.fullmatch(VER, v["name"])]
    key = lambda v: tuple(int(x) for x in v["name"].split("."))
    for a, b in zip(rel, rel[1:]):
        if key(a) <= key(b):
            problems.append("versions out of order: [%s] must come before [%s] (newest first, strictly descending)" % (b["name"], a["name"]))
    for v in rel:
        if v["bullets"] == 0:
            problems.append("[%s] has no entries" % v["name"])
    for v in versions:
        if v["name"] not in links:
            problems.append("missing link definition '[%s]: url' at the bottom" % v["name"])
    for k in links:
        if k not in names:
            problems.append("link '[%s]' has no matching version heading" % k)
    return versions, links, problems


def split_unreleased(text):
    """-> (before, body, after) around the [Unreleased] body; `after` starts at the next '## [' or the link block."""
    lines = text.splitlines(keepends=True)
    i = next((n for n, l in enumerate(lines) if l.startswith("## [Unreleased]")), None)
    if i is None:
        raise ValueError("no ## [Unreleased] heading")
    j = next((n for n in range(i + 1, len(lines)) if lines[n].startswith("## [") or LINK.match(lines[n])), len(lines))
    return "".join(lines[:i + 1]), "".join(lines[i + 1:j]), "".join(lines[j:])


def unreleased_body(text):
    """The [Unreleased] body stripped, or '' when it has no bullet."""
    body = split_unreleased(text)[1]
    return body.strip() + "\n" if re.search(r"^\s*[-*] ", body, re.M) else ""


def base_url():
    """The forge's repository URL for compare links: CHANGELOG_BASE (tests), else hk_forge_url + hk_forge_repo."""
    b = os.environ.get("CHANGELOG_BASE", "").strip().rstrip("/")
    if b:
        return b
    try:
        url, repo = hkconfig.get("hk_forge_url").rstrip("/"), hkconfig.get("hk_forge_repo")
    except hkconfig.ConfigUnavailable as e:
        raise ValueError("cannot read the configuration for the compare links: %s" % e)
    if not (url and repo):
        raise ValueError("no forge for the compare links: set hk_forge_url and hk_forge_repo in config/local.yml (docs/CONFIG.md)")
    return "%s/%s" % (url, repo)


def versions_table(lock_rows):
    """#278: the full pinned-version table a release section ends with (markdown, from versions.lock rows)."""
    out = ["", "Every pinned component in this release (versions.lock):", "",
           "| Component | Version | Commit / sha256 |", "|---|---|---|"]
    for r in lock_rows:
        fp = re.search(r"set ([0-9a-f]+)", r["sha256"])
        pin = (r["commit"][:12] if r["commit"] != "-" else ("hash set " + fp.group(1) if fp else
               r["sha256"].split()[0].split("=")[-1][:12] if r["sha256"] != "-" else "-"))
        out.append("| %s | %s | %s |" % (r["component"], r["version"], pin))
    return "\n".join(out) + "\n"


def _with_versions_table(body, table):
    """Put the table at the end of the body's ### Versions section (made at the end when there is none)."""
    lines = body.rstrip("\n").split("\n")
    k = next((i for i, l in enumerate(lines) if l.strip() == "### Versions"), None)
    if k is None:
        return body.rstrip("\n") + "\n\n### Versions\n" + table
    end = next((i for i in range(k + 1, len(lines)) if lines[i].startswith("### ")), len(lines))
    while end > k + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:end]) + "\n" + table + ("\n" + "\n".join(lines[end:]) + "\n" if end < len(lines) else "")


def release_text(text, version, date, lock_rows=None):
    """New file text: [Unreleased] body moved under '## [version] - date', empty [Unreleased], links updated.
    lock_rows (#278, versions.lock rows): the release section's ### Versions ends with the full version table.
    Returns (new_text, section_text) where section_text is the body (the Forgejo release body / tag message)."""
    body = unreleased_body(text)
    if not body:
        raise ValueError("[Unreleased] is empty: write the entries first")
    if lock_rows:
        body = _with_versions_table(body, versions_table(lock_rows))
    versions, links, problems = parse(text)
    if problems:
        raise ValueError("CHANGELOG.md does not lint: " + problems[0])
    prev = next((v["name"] for v in versions if re.fullmatch(VER, v["name"])), None)
    before, _, after = split_unreleased(text)
    k = next((n for n, l in enumerate(after.splitlines(keepends=True)) if LINK.match(l)), None)
    lines = after.splitlines(keepends=True)
    versions_part = "".join(lines[:k] if k is not None else lines)
    old_links = [l for l in lines[k:] if LINK.match(l) and not l.startswith("[Unreleased]:")] if k is not None else []
    BASE = base_url()
    new_link = ("[%s]: %s/compare/v%s...v%s" % (version, BASE, prev, version)) if prev else \
               ("[%s]: %s/releases/tag/v%s" % (version, BASE, version))
    # `before` ends with the Unreleased heading line; emit it, an empty body, then the new version
    out = before + "\n" + "## [%s] - %s\n\n%s\n" % (version, date, body)
    out += versions_part.lstrip("\n")
    out = out.rstrip("\n") + "\n\n[Unreleased]: %s/compare/v%s...HEAD\n%s\n" % (BASE, version, new_link)
    out += "".join(l if l.endswith("\n") else l + "\n" for l in old_links)
    return out, body


def versions_problems(text, old_lock, new_lock):
    """#278: components whose pin changed between two versions.lock texts and have no [Unreleased] ### Versions
    bullet naming them with the new version (an arrow when there was an old one; `removed` when it is gone)."""
    import versions as V   # tools/versions.py (same folder, on sys.path above)
    unreleased, released = _versions_bullets(text)
    out = []
    for name, old, new in V.changes(V.parse_lock(old_lock), V.parse_lock(new_lock)):
        want = "removed" if new is None else V.label(new)
        hit = lambda bs, arrow: [b for b in bs if re.search(r"(?<![\w.-])%s(?![\w.-])" % re.escape(name), b)
                                 and want in b and (not arrow or old is None or new is None or "→" in b or "->" in b)]
        if hit(released, False):              # already called out in a released section: never asked again
            continue
        if not hit(unreleased, True):
            out.append("versions.lock changed %s but [Unreleased] ### Versions has no line like %r"
                       % (name, V.changelog_line(name, old, new)))
    return out


def _versions_bullets(text):
    """-> (bullets of [Unreleased] ### Versions, bullets of every released section's ### Versions)."""
    unreleased, released, cur, sec = [], [], None, None
    for l in text.splitlines():
        m = H_VER.match(l) if l.startswith("## ") else None
        if m:
            cur, sec = m.group(1), None
        elif l.startswith("### "):
            sec = l[4:].strip()
        elif LINK.match(l):
            cur = sec = None
        elif cur and sec == "Versions" and re.match(r"^\s*[-*] ", l):
            (unreleased if cur == "Unreleased" else released).append(l)
    return unreleased, released


def base_lock(repo, base=None):
    """-> (versions.lock text at BASE or None, why None). BASE default: `git merge-base HEAD origin/main`, used only
    when it differs from HEAD (on main itself, or a branch with nothing of its own, there is nothing to compare).
    '' when BASE has no versions.lock yet (every component counts as new)."""
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if base is None:
        r = g("merge-base", "HEAD", "origin/main")
        if r.returncode != 0:
            return None, "no origin/main to compare with"
        base = r.stdout.strip()
        head = g("rev-parse", "HEAD").stdout.strip()
        if base == head:
            return None, "HEAD is origin/main's merge base (no commits of its own to compare)"
    r = g("show", "%s:versions.lock" % base)
    return (r.stdout if r.returncode == 0 else ""), ""


def has_entries(text, version):
    return any(v["name"] == version and v["bullets"] > 0 and v["date"] for v in parse(text)[0])


def check_tags(repo):
    """-> list of problems, one per failing tag (names the tag). Squash-merge safe: only tags and file contents."""
    repo = pathlib.Path(repo)
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    r = g("tag", "--list", "v[0-9]*")
    if r.returncode != 0:
        return ["git tag --list failed: %s" % r.stderr.strip()]
    tags = sorted(t for t in r.stdout.split() if re.fullmatch("v" + VER, t))
    try:
        now = (repo / "CHANGELOG.md").read_text()
    except OSError:
        now = None
    out = []
    for t in tags:
        v = t[1:]
        if now is None:
            out.append("%s: CHANGELOG.md is missing" % t)
        elif not any(x["name"] == v for x in parse(now)[0]):
            out.append("%s: CHANGELOG.md has no '## [%s] - date' section" % (t, v))
        elif not has_entries(now, v):
            out.append("%s: CHANGELOG.md section [%s] is empty or has no date" % (t, v))
        c = g("show", "%s:CHANGELOG.md" % t)
        if c.returncode != 0:
            out.append("%s: the tagged commit has no CHANGELOG.md (tag predates the changelog)" % t)
        elif not any(x["name"] == v for x in parse(c.stdout)[0]):
            out.append("%s: the tagged commit's CHANGELOG.md has no [%s] section (tag predates its section)" % (t, v))
        elif not has_entries(c.stdout, v):
            out.append("%s: the tagged commit's [%s] section is empty or has no date" % (t, v))
    return out


def main(argv):
    if not argv or argv[0] not in ("lint", "unreleased", "check-tags", "versions") or len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == "versions":
        repo = pathlib.Path(os.environ.get("CHANGELOG_REPO") or pathlib.Path(__file__).resolve().parent.parent)
        old, why = base_lock(repo, argv[1] if len(argv) == 2 else None)
        if old is None:
            print("SKIP changelog versions: %s, nothing checked" % why)
            print("RESULT: PASS changelog versions (skipped: %s)" % why)
            return 0
        try:
            new, text = (repo / "versions.lock").read_text(), (repo / "CHANGELOG.md").read_text()
        except OSError as e:
            print("RESULT: FAIL changelog versions: %s" % e)
            return 1
        problems = versions_problems(text, old, new)
        for p in problems:
            print("FAIL %s" % p)
        if problems:
            print("RESULT: FAIL changelog versions (%d component(s) without a ### Versions line)" % len(problems))
            return 1
        print("RESULT: PASS changelog versions (every changed pin has its ### Versions line)")
        return 0
    if argv[0] == "check-tags":
        repo = argv[1] if len(argv) == 2 else pathlib.Path(__file__).resolve().parent.parent
        problems = check_tags(repo)
        for p in problems:
            print("FAIL %s" % p)
        if problems:
            print("RESULT: FAIL changelog check-tags (%d problem(s))" % len(problems))
            return 1
        print("RESULT: PASS changelog check-tags")
        return 0
    path = pathlib.Path(argv[1]) if len(argv) == 2 else pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    try:
        text = path.read_text()
    except OSError as e:
        print("RESULT: FAIL changelog: %s" % e)
        return 1
    if argv[0] == "unreleased":
        print(unreleased_body(text), end="")
        return 0
    versions, links, problems = parse(text)
    for p in problems:
        print("FAIL %s: %s" % (path.name, p))
    if problems:
        print("RESULT: FAIL changelog lint (%d problem(s))" % len(problems))
        return 1
    print("RESULT: PASS changelog lint (%d version(s), Unreleased %s)" % (len(versions) - 1, "has entries" if unreleased_body(text) else "empty"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
