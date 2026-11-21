#!/usr/bin/env python3
"""Fail loudly when ES-DE did not render the requested theme (stdlib only).

check_log.py <es_log.txt> <theme-name>

ES-DE silently falls back to its built-in theme (Linear) when a theme is missing or broken,
and a half-broken theme still logs "Finished loading theme". So FAIL (exit 1) when:
  - the log never says: Finished loading theme "<theme-name>"   (fallback / theme not selected)
  - it finishes a DIFFERENT theme than requested                 (fallback)
  - any Error/Warn line mentions ThemeData or a theme            (parse error, missing file)
Exit 2 = log unreadable. Audio / VSync noise from the container is ignored.
"""
import re, sys

LINE = re.compile(r"^\w{3} +\d+ [\d:]+ (Error|Warn):\s+(.*)$")
DONE = re.compile(r'Finished loading theme "([^"]+)"')
THEMEY = re.compile(r"ThemeData|[Tt]heme")


def check(text, theme):
    problems = []
    loaded = DONE.findall(text)
    if not loaded:
        problems.append('no \'Finished loading theme\' line: ES-DE never loaded a theme')
    elif theme not in loaded:
        problems.append('FALLBACK: loaded theme %r instead of %r' % (loaded[-1], theme))
    for ln in text.splitlines():
        m = LINE.match(ln)
        if m and THEMEY.search(m.group(2)):
            problems.append("%s: %s" % (m.group(1), m.group(2)[:300]))
    return problems


def main(argv):
    if len(argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    try:
        text = open(argv[1], errors="replace").read()
    except OSError as e:
        sys.stderr.write("CHECK: cannot read %s: %s\n" % (argv[1], e))
        return 2
    problems = check(text, argv[2])
    if problems:
        print("CHECK: FAIL, theme %r did not render cleanly (%d problem(s)):" % (argv[2], len(problems)))
        seen = set()
        for p in problems:
            if p not in seen and len(seen) < 8:
                print("  - " + p)
            seen.add(p)
        return 1
    print("CHECK: OK, theme %r loaded with no theme errors" % argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
