#!/usr/bin/env python3
"""Doc gate (ticket #156, D3 of #103): the human docs say true things.

Over every git-tracked *.md (not CHANGELOG.md, docs/history/, docs/reference/ research notes, personal/,
.claude/team/RETRO.md, and not what test_no_local_values exempts for the private-value rule):
  1. every relative link resolves (the #anchor part is not checked);
  2. every `make X` / `make -C dir X` inside a code span or fence names a target that `make help` /
     `make -C dir help` lists (prose such as "make targets" is not looked at);
  3. every hk_* name is a declared variable (config/variables.d/*.yml); a miss prints a did-you-mean;
     `hk_game_*` style prefixes need one declared name with that prefix; PLACEHOLDERS are teaching examples;
  4. no private value (the PATTERNS of test_no_local_values) in a doc the community edition ships;
  5. README.md > 150 lines or docs/SETUP.md > 60 lines is a counted WARN (a SKIP line), not a fail;
  6. `make config-docs` output equals docs/CONFIG.md (rendered to a temp file, the tree is untouched).
Each rule has a planted broken case below that must FAIL. Plain python (hktest); a private-value needle is built
from fragments so this file stays clean.
"""
import difflib
import fnmatch
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
from hktest import check, main, skip  # noqa: E402
import test_no_local_values as nlv  # noqa: E402  (its PATTERNS and exempt(): one list, not a copy)

SKIP_GLOBS = ["CHANGELOG.md", "docs/history/*", "docs/reference/*", "personal/*", ".claude/team/RETRO.md"]
PLACEHOLDERS = {"hk_x", "hk_y", "hk_a", "hk_b"}  # example names in the "add a variable" steps and the HK_SET example
LENGTH_LIMITS = {"README.md": 150, "docs/SETUP.md": 60}

LINK = re.compile(r"\]\(([^)\s]+)")
FENCE = re.compile(r"^\s*(```|~~~)")
SPAN = re.compile(r"`([^`\n]+)`")
MAKE = re.compile(r"(?<![\w-])make\s+(?:-C\s+(\S+)\s+)?([a-z][a-z0-9_-]*)(?![\w=-])")
HKNAME = re.compile(r"(?<![A-Za-z0-9_])(hk_[a-z0-9_]+)(\*?)")
DECL = re.compile(r"^  (hk_[a-z0-9_]+):\s*$", re.M)
ALIASES = re.compile(r"^    aliases:\s*\[([^\]]*)\]", re.M)       # #240: a renamed variable's old name is declared too


def doc_files(root):
    out = subprocess.run(["git", "-C", root, "ls-files", "-z", "*.md"], check=True, capture_output=True).stdout
    names = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    return [p for p in names if not any(fnmatch.fnmatch(p, g) for g in SKIP_GLOBS) and os.path.isfile(os.path.join(root, p))]


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def code_text(text):
    """[(lineno, code)]: fenced lines and inline `spans`; prose is dropped."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
        elif fenced:
            out.append((n, line))
        else:
            out += [(n, m.group(1)) for m in SPAN.finditer(line)]
    return out


# ---- rule 1: links
def link_problems(root, rel, text):
    probs, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
        if fenced:
            continue
        for m in LINK.finditer(SPAN.sub("", line)):  # `[a-z](x)` in a code span is a regex, not a link
            t = m.group(1)
            if re.match(r"[a-z][a-z0-9+.-]*:", t, re.I) or t.startswith("#") or re.search(r"[{}\\$^|*]", t):
                continue  # http:, mailto:, same-page anchor, a regex or sudoers line
            path = t.split("#", 1)[0].split("?", 1)[0]
            if path and not os.path.exists(os.path.normpath(os.path.join(root, os.path.dirname(rel), path))):
                probs.append("%s:%d: dead link %s" % (rel, n, t))
    return probs


# ---- rule 2: make targets
def make_refs(text):
    return [(n, d, t) for n, code in code_text(text) for d, t in MAKE.findall(code)]


def help_targets(root, subdir=""):
    """Set of target names `make [-C subdir] help` lists, or None when make cannot run there."""
    cmd = ["make", "-s"] + (["-C", os.path.join(root, subdir)] if subdir else ["-C", root]) + ["help"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    return set(re.findall(r"^  ([a-z][a-z0-9_-]*)\s", r.stdout, re.M))


def make_problems(rel, text, targets_of):
    """targets_of(subdir) -> set | None; None = that folder has no make help (a finding)."""
    probs = []
    for n, d, t in make_refs(text):
        have = targets_of(d)
        if have is None:
            probs.append("%s:%d: make -C %s: `make help` does not run there" % (rel, n, d))
        elif t.endswith("-"):  # a prefix such as `make vm-`: some target must start with it
            if not any(x.startswith(t) for x in have):
                probs.append("%s:%d: no make target starts with `%s`" % (rel, n, t))
        elif t not in have:
            near = difflib.get_close_matches(t, sorted(have), 1, 0.6)
            probs.append("%s:%d: `make %s%s` is not a target%s" % (
                rel, n, "-C %s " % d if d else "", t, " (did you mean %s?)" % near[0] if near else ""))
    return probs


# ---- rule 3: hk_ names
def declared_names(root):
    d = os.path.join(root, "config", "variables.d")
    names = set()
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".yml"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                text = f.read()
                names |= set(DECL.findall(text))
                names |= {a.strip() for m in ALIASES.findall(text) for a in m.split(",") if a.strip()}
    return names


def hk_problems(rel, text, names):
    probs = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in HKNAME.finditer(line):
            name, star = m.group(1), m.group(2)
            if name in PLACEHOLDERS:
                continue
            if star or name.endswith("_"):  # a prefix such as hk_game_*
                if not any(x.startswith(name) for x in names):
                    probs.append("%s:%d: no variable starts with %s" % (rel, n, name))
            elif name not in names:
                near = difflib.get_close_matches(name, sorted(names), 1, 0.6)
                probs.append("%s:%d: %s is not a declared variable%s" % (
                    rel, n, name, " (did you mean %s?)" % near[0] if near else ""))
    return probs


# ---- rule 4: private values
def private_problems(rel, text):
    if nlv.exempt(rel):
        return []
    return ["%s:%d: [%s] %s" % (rel, n, rule, line.strip()[:80])
            for n, line in enumerate(text.splitlines(), 1) for rule, rx in nlv.PATTERNS if rx.search(line)]


# ---- rule 5: length
def length_warnings(root, files):
    warns = []
    for rel, limit in sorted(LENGTH_LIMITS.items()):
        if rel in files:
            n = len(read(root, rel).splitlines())
            if n > limit:
                warns.append("%s has %d lines (limit %d)" % (rel, n, limit))
    return warns


# ---- rule 6: CONFIG.md regenerates
def config_python():
    """A python that can import PyYAML (config.py needs it): this one, else the config venv; None = cannot run."""
    for py in (sys.executable, os.path.expanduser("~/.venvs/coin-dojo/bin/python")):
        if os.path.exists(py) and subprocess.run([py, "-c", "import yaml"], capture_output=True).returncode == 0:
            return py
    return None


def render_config_docs(root, py):
    with tempfile.TemporaryDirectory(prefix="hk-docs-") as d:
        out = os.path.join(d, "CONFIG.md")
        r = subprocess.run([py, os.path.join(root, "tools", "config.py"), "docs", "--out", out],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return None, r.stdout + r.stderr
        with open(out, encoding="utf-8") as f:
            return f.read(), ""


def config_docs_diff(current, fresh):
    return "".join(list(difflib.unified_diff(current.splitlines(True), fresh.splitlines(True), "docs/CONFIG.md", "regenerated", n=1))[:30])


# ---- the repo
def test_links_resolve():
    probs = [p for rel in doc_files(ROOT) for p in link_problems(ROOT, rel, read(ROOT, rel))]
    for p in probs:
        print("  " + p)
    check(not probs, "every relative link in the docs resolves (%d dead)" % len(probs))


def test_make_targets_exist():
    cache = {}

    def targets_of(d):
        if d not in cache:
            cache[d] = help_targets(ROOT, d)
        return cache[d]

    files, probs, refs = doc_files(ROOT), [], 0
    for rel in files:
        text = read(ROOT, rel)
        refs += len(make_refs(text))
        probs += make_problems(rel, text, targets_of)
    for p in probs:
        print("  " + p)
    check(refs > 0 and targets_of("") and "help" in targets_of(""), "`make help` runs and the docs name make targets (%d refs)" % refs)
    check(not probs, "every `make X` in the docs is a listed target (%d wrong)" % len(probs))


def test_hk_names_declared():
    names = declared_names(ROOT)
    check(len(names) > 20, "found the declared hk_ variables (%d)" % len(names))
    probs = [p for rel in doc_files(ROOT) for p in hk_problems(rel, read(ROOT, rel), names)]
    for p in probs:
        print("  " + p)
    check(not probs, "every hk_ name in the docs is declared (%d unknown)" % len(probs))


def test_no_private_values_in_docs():
    probs = [p for rel in doc_files(ROOT) for p in private_problems(rel, read(ROOT, rel))]
    for p in probs:
        print("  " + p)
    check(not probs, "no private value in a doc the community edition ships (%d hits)" % len(probs))


def test_length_warnings():
    warns = length_warnings(ROOT, doc_files(ROOT))
    for w in warns:
        skip("WARN " + w)
    check(True, "length check ran (%d warning(s), never a fail)" % len(warns))


def test_config_docs_regenerates_clean():
    py = config_python()
    if py is None:
        skip("config-docs: no python with PyYAML (run `make config-venv`)")
        return
    fresh, err = render_config_docs(ROOT, py)
    check(fresh is not None, "config.py docs renders to a temp file " + err[:200])
    if fresh is not None:
        diff = config_docs_diff(read(ROOT, "docs/CONFIG.md"), fresh)
        if diff:
            print(diff)
        check(not diff, "docs/CONFIG.md equals `make config-docs` output (run it and commit)")


# ---- planted broken cases: each rule must fail on a bad doc
def _tree(files):
    d = tempfile.mkdtemp(prefix="hk-docs-t-")
    for rel, body in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)
    return d


def test_selftest_dead_link_fails():
    d = _tree({"docs/ok.md": "x\n", "docs/a.md": "[ok](ok.md#top) [up](../README.md) [web](https://x.test/y) [self](#a)\n",
               "README.md": "r\n"})
    check(link_problems(d, "docs/a.md", read(d, "docs/a.md")) == [], "good links (anchor, ../, http, #self) pass")
    bad = link_problems(d, "docs/a.md", "see [gone](missing.md) and `[a-z](nope.md)` regex\n")
    check(len(bad) == 1 and "missing.md" in bad[0], "a planted dead link is reported, a code-span regex is not: %s" % bad)
    check(link_problems(d, "docs/a.md", "```\n[x](nope.md)\n```\n") == [], "links inside a fence are ignored")


def test_selftest_make_nosuch_fails():
    have = {"": {"help", "test", "deploy"}, "skills/c": {"test"}, "skills/none": None}
    f = lambda d: have.get(d)  # noqa: E731
    check(make_problems("a.md", "run `make deploy` then `make -C skills/c test`\n```\nmake test\n```\n", f) == [],
          "real targets pass")
    check(make_problems("a.md", "the make targets are listed; make sure it works; make nosuch\n", f) == [],
          "prose (no code span) is not a target reference")
    bad = make_problems("a.md", "run `make nosuch`\n", f)
    check(len(bad) == 1 and "nosuch" in bad[0], "a planted `make nosuch` is reported: %s" % bad)
    near = make_problems("a.md", "run `make deplo`\n", f)
    check(len(near) == 1 and "did you mean deploy" in near[0], "a typo gets a did-you-mean: %s" % near)
    f2 = lambda d: {"vm-up", "vm-down"}  # noqa: E731
    check(make_problems("a.md", "the `make vm-` family\n", f2) == [], "a `make vm-` prefix with matching targets passes")
    pre = make_problems("a.md", "the `make zz-` family\n", f2)
    check(len(pre) == 1, "a prefix nobody has is reported: %s" % pre)
    sub = make_problems("a.md", "`make -C skills/c nosuch`\n", f)
    check(len(sub) == 1, "a missing sub-folder target is reported: %s" % sub)
    nohelp = make_problems("a.md", "`make -C skills/none test`\n", f)
    check(len(nohelp) == 1 and "does not run" in nohelp[0], "a folder without make help is reported: %s" % nohelp)


def test_selftest_unknown_hk_name_fails():
    names = {"hk_game_procs", "hk_game_patterns", "hk_cabinet_host"}
    check(hk_problems("a.md", "set `hk_cabinet_host`, `hk_game_*`, and an example hk_x\n", names) == [],
          "declared names, a prefix and a placeholder pass")
    bad = hk_problems("a.md", "set hk_nosuch=1\n", names)
    check(len(bad) == 1 and "hk_nosuch" in bad[0], "a planted hk_nosuch is reported: %s" % bad)
    near = hk_problems("a.md", "set hk_cabinet_hots\n", names)
    check(len(near) == 1 and "did you mean hk_cabinet_host" in near[0], "a typo gets a did-you-mean: %s" % near)
    pre = hk_problems("a.md", "all hk_nope_* names\n", names)
    check(len(pre) == 1, "a prefix nobody has is reported: %s" % pre)


def test_selftest_private_value_fails():
    planted = "ssh in and open /home/" + "cap" + "com/.config"      # a generic rule: the community tree has no private list
    bad = private_problems("docs/a.md", planted + "\n")
    check(len(bad) == 1 and "coindojo-home" in bad[0], "a planted cabinet home path in a doc is reported: %s" % bad)
    check(private_problems("docs/a.md", "ssh <cabinet host> /usr/share/doc\n") == [], "neutral wording passes")
    check(private_problems("personal/a.md", planted + "\n") == [], "personal/ is exempt (not shipped)")


def test_selftest_long_doc_warns():
    d = _tree({"README.md": "l\n" * 151, "docs/SETUP.md": "l\n" * 60})
    w = length_warnings(d, ["README.md", "docs/SETUP.md"])
    check(len(w) == 1 and "README.md has 151" in w[0], "README over 150 warns, SETUP at 60 does not: %s" % w)
    d2 = _tree({"README.md": "l\n" * 10, "docs/SETUP.md": "l\n" * 61})
    w2 = length_warnings(d2, ["README.md", "docs/SETUP.md"])
    check(len(w2) == 1 and "SETUP.md has 61" in w2[0], "SETUP over 60 warns: %s" % w2)


def test_selftest_stale_config_docs_fails():
    cur = "# x\n| hk_a | 1 |\n"
    check(config_docs_diff(cur, cur) == "", "identical text: no diff")
    check("hk_b" in config_docs_diff(cur, cur + "| hk_b | 2 |\n"), "a stale CONFIG.md shows a diff")


if __name__ == "__main__":
    main(globals())
