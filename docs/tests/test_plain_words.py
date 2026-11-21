#!/usr/bin/env python3
"""Plain words gate (#282): customer copy uses no tech jargon without a plain gloss.

The cheap, automatic half of the Everyday Owner review (AGENTS.md "Team", .claude/team/PROCESS.md sec. 5).
Scope: docs/START.md, docs/SCRAPING.md and the booklet (docs/manual/*.md, docs/manual/*.html), each when present.

Rule: a sentence that uses a JARGON term must contain the term's gloss phrase from docs/tests/plain_words.tsv, or
the next sentence must (case-insensitive substring). A term without a gloss line may not appear at all.
Not scanned: URLs and link targets (`smb://...` is something to type, not a word to understand) and, in HTML, tags,
styles, scripts and data: URIs. Sentences: blocks split at blank lines and at list items, headings, table rows and
quotes; then at `.`, `!` or `?` followed by a space.

Broken cases (must be flagged): an unglossed term, a gloss two sentences away, a term with no gloss line, `make`
as a typed command, a gloss line for an unknown term, a malformed gloss line.

Run: python3 docs/tests/test_plain_words.py          (the gate)
     python3 docs/tests/test_plain_words.py FILE...  (check any files, e.g. a draft; exit 1 on a finding)
"""
import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

GLOSS_FILE = os.path.join(HERE, "plain_words.tsv")
SCOPE = ["docs/START.md", "docs/SCRAPING.md"]
MANUAL_DIR = "docs/manual"

# term -> (pattern, flags). Word boundaries; plain English uses stay legal ("make an account" is fine, `make x` not).
JARGON = {
    "SSH": (r"\bssh\b", re.I),
    "terminal": (r"\bterminals?\b", re.I),
    "command line": (r"\bcommand[ -]lines?\b", re.I),
    "CLI": (r"\bCLI\b", 0),
    "repo": (r"\brepo(s|sitory|sitories)?\b", re.I),
    "git": (r"\bgit\b", re.I),
    "sudo": (r"\bsudo\b", re.I),
    "mDNS": (r"\bmdns\b", re.I),
    "SMB": (r"\bsmb\b", re.I),
    "YAML": (r"\bya?ml\b", re.I),
    "chroot": (r"\bchroot\b", re.I),
    "ext4": (r"\bext4\b", re.I),
    "dd": (r"\bdd\b", re.I),
    "make": (r"`make\b", 0),
    "Ansible": (r"\bansible\b", re.I),
    "deploy": (r"\bdeploy(s|ed|ing|ment)?\b", re.I),
    "SHA": (r"\bsha(1|256|512)?(sum)?\b", re.I),
    "checksum": (r"\bchecksums?\b", re.I),
    "partition": (r"\bpartitions?\b", re.I),
    "mount": (r"\bmount(s|ed|ing)?\b", re.I),
    "shell": (r"\bshell\b", re.I),
    "scp": (r"\bscp\b", re.I),
    "rsync": (r"\brsync\b", re.I),
    "kernel": (r"\bkernels?\b", re.I),
    "config file": (r"\bconfig(uration)? files?\b", re.I),
}
RX = {t: re.compile(p, f) for t, (p, f) in JARGON.items()}

URL = re.compile(r"\]\([^)]*\)|<[a-z][a-z0-9+.-]*:[^>\s]*>|\b[a-z][a-z0-9+.-]*://\S+", re.I)
BLOCK_START = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)")
LIST_MARK = re.compile(r"^\s*(\d+[.)]|[-*+])\s+")


def load_glosses(text):
    """{term: gloss} from plain_words.tsv text; ValueError on a malformed line or a term JARGON does not know."""
    out = {}
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        cols = raw.split("\t")
        if len(cols) != 2 or not cols[0].strip() or not cols[1].strip():
            raise ValueError("plain_words.tsv:%d: want term<TAB>gloss phrase, got %r" % (n, raw))
        if cols[0] not in JARGON:
            raise ValueError("plain_words.tsv:%d: %r is not a JARGON term" % (n, cols[0]))
        out[cols[0]] = cols[1].strip()
    return out


def html_text(s):
    s = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", s)
    s = re.sub(r"data:[^\"')\s]+", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|li|h[1-6]|div|tr|td|th|section)>", "\n\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def sentences(text):
    """The text's sentences in order (URLs and link targets removed)."""
    text = URL.sub(" ", text)
    blocks, cur = [], []
    for line in text.splitlines():
        if not line.strip() or BLOCK_START.match(line):
            if cur:
                blocks.append(" ".join(cur))
            cur = [LIST_MARK.sub("", line).strip()] if line.strip() else []
        else:
            cur.append(line.strip())
    if cur:
        blocks.append(" ".join(cur))
    out = []
    for b in blocks:
        out.extend(s for s in re.split(r"(?<=[.!?])\s+", b) if s.strip())
    return out


def problems(text, glosses, name="text"):
    """['name: "term" without its gloss "...": <sentence>'] for each unglossed use."""
    sents = sentences(text)
    out = []
    for i, s in enumerate(sents):
        for term, rx in RX.items():
            if not rx.search(s):
                continue
            gloss = glosses.get(term)
            near = (s + " " + (sents[i + 1] if i + 1 < len(sents) else "")).lower()
            if gloss is None:
                out.append('%s: "%s" has no plain gloss in plain_words.tsv, so it may not appear: %s'
                           % (name, term, s[:120]))
            elif gloss.lower() not in near:
                out.append('%s: "%s" without its gloss "%s" in this or the next sentence: %s'
                           % (name, term, gloss, s[:120]))
    return out


def read_doc(path):
    with open(path, encoding="utf-8") as f:
        s = f.read()
    return html_text(s) if path.endswith(".html") else s


def scope_files(root=ROOT):
    files = [p for p in SCOPE if os.path.isfile(os.path.join(root, p))]
    mdir = os.path.join(root, MANUAL_DIR)
    if os.path.isdir(mdir):
        files += sorted(MANUAL_DIR + "/" + f for f in os.listdir(mdir) if f.endswith((".md", ".html")))
    return files


def glosses():
    with open(GLOSS_FILE, encoding="utf-8") as f:
        return load_glosses(f.read())


# ---- the gate
def test_gloss_file_loads():
    try:
        g = glosses()
    except ValueError as e:
        check(False, str(e))
        return
    check(len(g) > 0, "plain_words.tsv loads (%d glosses)" % len(g))


def test_customer_copy_is_plain():
    files = scope_files()
    for p in SCOPE:
        if p not in files:
            skip("%s not in the tree yet" % p)
    if not any(f.startswith(MANUAL_DIR) for f in files):
        skip("%s (the booklet) not in the tree yet" % MANUAL_DIR)
    g = glosses()
    for rel in files:
        probs = problems(read_doc(os.path.join(ROOT, rel)), g, rel)
        check(not probs, "%s: no unglossed jargon%s" % (rel, "".join("\n    " + p for p in probs)))


# ---- broken cases (each must be flagged)
G = {"SMB": "network folder", "terminal": "window where you type commands"}


def test_broken_unglossed_term():
    check(problems("Open a terminal and type the line.", G), "BROKEN: a term without its gloss is flagged")


def test_broken_term_without_gloss_line():
    p = problems("Log in over SSH with your key.", G)
    check(p and "no plain gloss" in p[0], "BROKEN: a term with no gloss line may not appear")


def test_broken_gloss_too_far():
    t = "Open the SMB share. Then wait. It is the network folder."
    check(problems(t, G), "BROKEN: a gloss two sentences later does not count")


def test_broken_make_as_command():
    check(problems("Run `make deploy` from the folder.", {"deploy": "x"}), "BROKEN: `make` typed as a command")


def test_broken_gloss_file_lines():
    for bad, why in [("nosuchterm\tsome gloss\n", "unknown term"), ("SMB network folder\n", "no tab"),
                     ("SMB\t\n", "empty gloss")]:
        try:
            load_glosses(bad)
            check(False, "BROKEN: gloss line with %s is refused" % why)
        except ValueError:
            check(True, "BROKEN: gloss line with %s is refused" % why)


# ---- what must pass
def test_glossed_terms_pass():
    check(not problems("Open SMB, the network folder on the cabinet.", G), "a gloss in the same sentence passes")
    check(not problems("Open a terminal. That is the window where you type commands.", G),
          "a gloss in the next sentence passes")


def test_plain_english_passes():
    t = ("Make an account at the site. Type `smb://coindojo.local` and press Enter. "
         "See [the guide](https://example.org/ssh/git/sudo). Drag games into the roms folder.")
    check(not problems(t, G), "plain words, typed URLs and link targets pass")


def test_html_booklet_text():
    page = ('<html><style>.ssh{}</style><body><h2>Setup</h2><p>Open a <b>terminal</b>.</p>'
            '<img src="data:image/png;base64,c3NoIGdpdA=="></body></html>')
    p = problems(html_text(page), G)
    check(len(p) == 1 and '"terminal"' in p[0], "booklet HTML: text scanned, styles and data URIs not")


def test_blocks_split_at_list_items():
    s = sentences("1. Pick SETTINGS\n2. Press LK\n\nA long\nline wraps.")
    check(s == ["Pick SETTINGS", "Press LK", "A long line wraps."], "list items are separate sentences: %r" % s)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        found = []
        for path in sys.argv[1:]:
            found += problems(read_doc(path), glosses(), path)
        print("\n".join(found))
        print("RESULT: %s" % ("FAIL (%d found)" % len(found) if found else "PASS"))
        sys.exit(1 if found else 0)
    main(globals())
