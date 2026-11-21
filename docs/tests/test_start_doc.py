#!/usr/bin/env python3
"""The beginner guide stays short (#268): docs/START.md is a 2 to 3 minute read for an owner with no terminal.

  1. at most MAX_WORDS words and MAX_STEPS numbered steps (at least one);
  2. no terminal in it: no code block and no `make`, `ssh`, `sudo` or `passwd` command;
  3. README.md points new readers at it: a "New here?" line near the top, and the first row of the docs table.
Each rule has a planted broken case that must FAIL. Plain python (hktest), stdlib only.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main  # noqa: E402

START = "docs/START.md"
MAX_WORDS = 400   # the maintainer 2026-11-19: "at most 2-3 min read"; about 350 words is the target
MAX_STEPS = 7
TOP = 20          # the "New here?" line sits on the README's first screen
STEP = re.compile(r"^\d+\. ", re.M)
TERMINAL = re.compile(r"^```|\b(make|ssh|sudo|passwd|smbpasswd)\s+\S", re.M)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def length_problems(text):
    probs = []
    words, steps = len(text.split()), len(STEP.findall(text))
    if words > MAX_WORDS:
        probs.append("%d words > %d" % (words, MAX_WORDS))
    if not 1 <= steps <= MAX_STEPS:
        probs.append("%d numbered steps, want 1 to %d" % (steps, MAX_STEPS))
    return probs


def terminal_problems(text):
    return ["a terminal command or code block: %r" % m.group(0) for m in TERMINAL.finditer(text)]


def readme_problems(readme):
    probs = []
    if not any("New here?" in line and "](%s)" % START in line for line in readme.splitlines()[:TOP]):
        probs.append("no 'New here?' line linking %s in the first %d lines" % (START, TOP))
    rows = [line for line in readme.split("## Documentation", 1)[-1].splitlines() if line.startswith("| ")]
    if len(rows) < 2 or "](%s)" % START not in rows[1]:
        probs.append("%s is not the first row of the docs table" % START)
    return probs


def test_start_is_short():
    text = read(START)
    check(length_problems(text) == [], "%s: %d words, %d steps %s" % (
        START, len(text.split()), len(STEP.findall(text)), length_problems(text)))
    check(length_problems(text + " word" * MAX_WORDS) != [], "BROKEN: a guide over %d words is reported" % MAX_WORDS)
    extra = "".join("\n%d. one more step\n" % n for n in range(7, 7 + MAX_STEPS))
    check(length_problems(text + extra) != [], "BROKEN: a guide with more than %d steps is reported" % MAX_STEPS)
    check(length_problems("no steps at all") != [], "BROKEN: a guide without numbered steps is reported")


def test_start_needs_no_terminal():
    text = read(START)
    check(terminal_problems(text) == [], "%s asks for no terminal command %s" % (START, terminal_problems(text)))
    check(terminal_problems(text + "\n```\nssh coindojo@coindojo.local\n```\n") != [],
          "BROKEN: a code block with an ssh command is reported")


def test_readme_points_here():
    readme = read("README.md")
    check(readme_problems(readme) == [], "README: 'New here?' line and first docs-table row %s" % readme_problems(readme))
    check(readme_problems(readme.replace("New here?", "Fresh?")) != [], "BROKEN: no 'New here?' line is reported")
    row = next(line for line in readme.splitlines() if line.startswith("| ") and "](%s)" % START in line)
    moved = readme.replace(row + "\n", "", 1).replace("| Contribute", row + "\n| Contribute", 1)
    check(readme_problems(moved) != [], "BROKEN: START.md moved down the docs table is reported")


if __name__ == "__main__":
    main(globals())
