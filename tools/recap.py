#!/usr/bin/env python3
"""recap: compare the PLAY RECAP and the changed tasks of `make deploy` dry-run logs (#136).

`make deploy` prints Ansible's default stdout callback (ansible/ansible.cfg: stdout_callback = default); save it with
`make deploy 2>&1 | tee A.log`. This tool reads two such logs (or one, with --zero) and reports what differs.

Usage:
  tools/recap.py A.log B.log    exit 0 when both logs recap the same counts and the same changed tasks,
                                1 when they differ (each difference is named), 2 when a log is missing or malformed
  tools/recap.py --zero B.log   exit 0 only when B recaps changed=0, failed=0 and unreachable=0 for every host
                                (what `make rollout` needs from its second dry run); 1 names the hosts and the changed
                                tasks; 2 when the log is missing or malformed

What is parsed (everything else, e.g. --diff bodies and YAML results, is ignored):
  TASK [role : name] ****            the current task (RUNNING HANDLER [role : name] for handlers)
  changed: [host] => (item=x)        one changed result (host, task, item); `host -> delegate` counts as host
  PLAY RECAP ****                    then one `host : ok=N changed=N unreachable=N failed=N ...` line per host
A log with several PLAY RECAP blocks (several playbooks) sums the counts per host.
Malformed: no PLAY RECAP, a recap block without hosts, a recap line that is not `host : key=N ...`, or a recap line
missing ok/changed/unreachable/failed.
Stdlib only.
"""
import argparse
import collections
import re
import sys

KEYS = ("ok", "changed", "unreachable", "failed", "skipped", "rescued", "ignored")
REQUIRED = ("ok", "changed", "unreachable", "failed")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TASK_RE = re.compile(r"^(TASK|RUNNING HANDLER) \[(?P<name>.*)\] \*+\s*$")
RECAP_RE = re.compile(r"^PLAY RECAP \*+\s*$")
CHANGED_RE = re.compile(r"^changed: \[(?P<host>[^\]]+)\](?P<rest>.*)$")
HOSTLINE_RE = re.compile(r"^(?P<host>\S+)\s+:\s+(?P<pairs>\S.*?)\s*$")
PAIR_RE = re.compile(r"^(?P<k>[a-z]+)=(?P<v>-?\d+)$")


class Malformed(Exception):
    pass


class Log:
    def __init__(self, path):
        self.path = path
        self.recap = {}                       # host -> {key: int}
        self.changed = []                     # ordered list of "[host] task (item=x)"


def _item(rest):
    """' => (item=nes) => ' -> 'nes'; '' -> None. Items may hold parentheses: (item=(censored due to no_log))."""
    rest = rest.strip()
    if not rest.startswith("=> (item="):
        return None
    body = rest[len("=> (item="):]
    if body.endswith("=>"):
        body = body[:-2].rstrip()
    return body[:-1] if body.endswith(")") else body


def parse(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise Malformed("%s: cannot read (%s)" % (path, e.strerror or e))
    log = Log(path)
    task = None
    in_recap = False
    recaps = 0
    hosts_in_block = 0
    for n, raw in enumerate(lines, 1):
        line = ANSI.sub("", raw).rstrip("\r")
        if in_recap:
            if not line.strip():
                if hosts_in_block:
                    in_recap = False
                continue
            m = HOSTLINE_RE.match(line)
            if not m:
                if hosts_in_block:          # the block ended without a blank line; this line is not ours
                    in_recap = False
                else:
                    raise Malformed("%s:%d: PLAY RECAP line is not 'host : key=N ...': %r" % (path, n, line[:120]))
            else:
                counts = {}
                for pair in m.group("pairs").split():
                    p = PAIR_RE.match(pair)
                    if not p:
                        raise Malformed("%s:%d: bad recap field %r" % (path, n, pair))
                    counts[p.group("k")] = int(p.group("v"))
                missing = [k for k in REQUIRED if k not in counts]
                if missing:
                    raise Malformed("%s:%d: recap for %s lacks %s" % (path, n, m.group("host"), ", ".join(missing)))
                tot = log.recap.setdefault(m.group("host"), dict.fromkeys(KEYS, 0))
                for k, v in counts.items():
                    tot[k] = tot.get(k, 0) + v
                hosts_in_block += 1
                continue
        if RECAP_RE.match(line):
            in_recap, hosts_in_block = True, 0
            recaps += 1
            continue
        m = TASK_RE.match(line)
        if m:
            task = ("handler: " if m.group(1) == "RUNNING HANDLER" else "") + m.group("name")
            continue
        m = CHANGED_RE.match(line)
        if m:
            host = m.group("host").split(" -> ")[0].strip()
            item = _item(m.group("rest"))
            log.changed.append("[%s] %s%s" % (host, task or "(no task)", "" if item is None else " (item=%s)" % item))
    if not recaps:
        raise Malformed("%s: no PLAY RECAP (truncated or not an ansible log)" % path)
    if not log.recap:
        raise Malformed("%s: PLAY RECAP has no host lines" % path)
    return log


def _only(a, b):
    """Entries of list a not matched in list b (multiset difference), in a's order."""
    left = collections.Counter(b)
    out = []
    for x in a:
        if left[x]:
            left[x] -= 1
        else:
            out.append(x)
    return out


def compare(a, b):
    """Return the list of difference lines (empty = equal)."""
    diffs = []
    for host in sorted(set(a.recap) | set(b.recap)):
        if host not in b.recap:
            diffs.append("recap: host %s only in A" % host)
        elif host not in a.recap:
            diffs.append("recap: host %s only in B" % host)
        else:
            for k in KEYS:
                if a.recap[host].get(k, 0) != b.recap[host].get(k, 0):
                    diffs.append("recap: %s %s=%d in A, %d in B" % (host, k, a.recap[host].get(k, 0), b.recap[host].get(k, 0)))
    diffs += ["changed only in A: %s" % x for x in _only(a.changed, b.changed)]
    diffs += ["changed only in B: %s" % x for x in _only(b.changed, a.changed)]
    return diffs


def zero(b):
    """Return the list of problems that stop B from being a clean second dry run (empty = clean)."""
    probs = []
    for host in sorted(b.recap):
        bad = ["%s=%d" % (k, b.recap[host][k]) for k in ("changed", "failed", "unreachable") if b.recap[host][k]]
        if bad:
            probs.append("recap: %s %s" % (host, " ".join(bad)))
    probs += ["changed: %s" % x for x in b.changed]
    return probs


def main(argv=None):
    ap = argparse.ArgumentParser(prog="recap.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--zero", action="store_true", help="one log: exit 1 unless it recaps changed=0 everywhere")
    ap.add_argument("logs", nargs="+", metavar="LOG")
    args = ap.parse_args(argv)
    want = 1 if args.zero else 2
    if len(args.logs) != want:
        ap.print_usage(sys.stderr)
        print("recap.py: error: %s takes %d log%s" % ("--zero" if args.zero else "compare", want, "" if want == 1 else "s"),
              file=sys.stderr)
        return 2
    try:
        logs = [parse(p) for p in args.logs]
    except Malformed as e:
        print("recap: MALFORMED %s" % e)
        return 2
    if args.zero:
        probs = zero(logs[0])
        for p in probs:
            print(p)
        if probs:
            print("recap: NOT ZERO %s (%d host problem(s), %d changed task(s))" % (
                args.logs[0], sum(p.startswith("recap:") for p in probs), len(logs[0].changed)))
            return 1
        print("recap: ZERO %s (%d host(s), changed=0 failed=0 unreachable=0)" % (args.logs[0], len(logs[0].recap)))
        return 0
    diffs = compare(*logs)
    for d in diffs:
        print(d)
    if diffs:
        nrec = sum(d.startswith("recap:") for d in diffs)
        print("recap: DIFFERENT (%d recap count(s), %d changed task(s))" % (nrec, len(diffs) - nrec))
        return 1
    print("recap: EQUAL (%d host(s), %d changed task(s) in both)" % (len(logs[0].recap), len(logs[0].changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
