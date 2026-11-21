#!/usr/bin/env python3
"""tools/commitlint.py - Conventional Commits with a ticket number (#99). Python 3.11 stdlib only.

  tools/commitlint.py check MSGFILE   lint one commit message (the commit-msg hook); exit 1 on a bad one
  tools/commitlint.py range A..B      lint every non-merge commit in the range; one line per bad commit

Format:  type(scope): #N summary  + blank line + body (trailers last: Co-Authored-By, Closes #N, Refs #N)
  type   feat fix perf refactor style docs test build ci chore revert (nothing else, lowercase)
  scope  optional, lowercase [a-z0-9._/-]+
  #N     ticket number right after ": ", required, except `chore(release): vX.Y.Z`, git's own `Revert "..."`,
         merge commits, or a body line `Refs: none`
  summary non-empty, no trailing period, whole subject <= 72 chars
"""
import re
import subprocess
import sys

TYPES = ("feat", "fix", "perf", "refactor", "style", "docs", "test", "build", "ci", "chore", "revert")
MAX_SUBJECT = 72
SCOPE_RE = r"[a-z0-9._/-]+"
HEAD_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?(?P<colon>:)(?P<sp> ?)(?P<rest>.*)$")
RELEASE_RE = re.compile(r"^chore\(release\): v\d+\.\d+\.\d+$")


def _head(subject, need_ticket):
    """Reasons for a subject; need_ticket False = number optional (titles, Refs: none)."""
    m = HEAD_RE.match(subject)
    if not m:
        return ["subject is not `type(scope): summary` (types: %s)" % " ".join(TYPES)]
    out = []
    t = m.group("type")
    if t not in TYPES:
        hint = " (must be lowercase)" if t.lower() in TYPES else ""
        out.append("unknown type `%s`%s; use one of: %s" % (t, hint, " ".join(TYPES)))
    sc = m.group("scope")
    if sc is not None and not re.fullmatch(SCOPE_RE, sc):
        out.append("bad scope `%s` (lowercase [a-z0-9._/-]+, not empty)" % sc)
    if not m.group("sp"):
        out.append("missing space after the colon")
    rest = m.group("rest").strip()
    if need_ticket:
        tm = re.match(r"#(\d+)(?: (.*))?$", rest)
        if not tm:
            out.append("missing ticket `#N` right after `: ` (or a `Refs: none` body line)")
            summary = rest
        else:
            summary = (tm.group(2) or "").strip()
    else:
        summary = rest
    if not summary:
        out.append("empty summary")
    if subject.rstrip().endswith("."):
        out.append("summary ends with a period")
    if len(subject) > MAX_SUBJECT:
        out.append("subject is %d chars (max %d)" % (len(subject), MAX_SUBJECT))
    return out


def lint_message(text):
    """List of reasons the message is bad; empty = good."""
    lines = [l for l in text.splitlines() if not l.startswith("#")]   # git strips comment lines
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["empty commit message"]
    subject = lines[0]
    if subject.startswith("Merge ") or subject.startswith('Revert "'):
        return []
    if RELEASE_RE.match(subject):
        return []
    out = []
    refs_none = any(l.strip() == "Refs: none" for l in lines[1:])
    out += _head(subject, need_ticket=not refs_none)
    if len(lines) > 1 and lines[1].strip():
        out.append("line 2 must be blank (subject, blank line, body)")
    return out


def lint_title(title):
    """Issue titles: same form, no number required."""
    return _head(title, need_ticket=False)


def cmd_check(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        bad = lint_message(f.read())
    for r in bad:
        print("commitlint: %s" % r, file=sys.stderr)
    if bad:
        print("commitlint: format is `type(scope): #N summary` (see AGENTS.md; `make commit` builds it)", file=sys.stderr)
        print("RESULT: FAIL commitlint", file=sys.stderr)
        return 1
    return 0


def cmd_range(rng):
    p = subprocess.run(["git", "log", "--no-merges", "--format=%H%x00%B%x01", rng], capture_output=True, text=True)
    if p.returncode != 0:
        print("FAIL git log %s: %s" % (rng, p.stderr.strip()), file=sys.stderr)
        print("RESULT: FAIL commitlint range")
        return 2
    total = bad = 0
    for rec in p.stdout.split("\x01"):
        if not rec.strip():
            continue
        sha, _, msg = rec.lstrip("\n").partition("\x00")
        total += 1
        why = lint_message(msg)
        if why:
            bad += 1
            subj = msg.strip().splitlines()[0] if msg.strip() else ""
            print("%s %s  <- %s" % (sha[:8], subj, "; ".join(why)))
    print("RESULT: %s commitlint range %s: %d commit(s), %d bad" % ("FAIL" if bad else "PASS", rng, total, bad))
    return 1 if bad else 0


def main(argv):
    if len(argv) == 3 and argv[1] == "check":
        return cmd_check(argv[2])
    if len(argv) == 3 and argv[1] == "range":
        return cmd_range(argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
