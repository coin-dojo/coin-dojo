#!/usr/bin/env python3
"""The own-lists guide stays true (#280): docs/LISTS.md's example is a real series.tsv and the guide stays short.

  1. the example file (first code block under "## The list file") loads with the real loader
     (skills/collections/nested.py load_series) and builds the folders it promises (build_series on sample games);
  2. the AI prompt (the next code block) names every system the launcher knows (launch.py CORE_BY_SYSTEM), asks for
     TAB separators and the 3-game minimum;
  3. the guide is at most MAX_WORDS words, and START.md and FAQ.md link it.
Broken cases (must be flagged): spaces instead of tabs, a bad regex, a duplicate folder name, a prompt missing a
system, a guide over the word limit, a missing link. Plain python (hktest), stdlib only, temp files only.
"""
import ast
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(ROOT, "skills", "collections"))
from hktest import check, main  # noqa: E402
import nested  # noqa: E402

LISTS = "docs/LISTS.md"
LINKED_FROM = ["docs/START.md", "docs/FAQ.md"]
MAX_WORDS = 500  # ticket #280: "<=500 words"
FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.S | re.M)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def blocks(text):
    """(example file, AI prompt): the first two code blocks after '## The list file'."""
    found = FENCE.findall(text.split("## The list file", 1)[-1])
    return (found + ["", ""])[:2]


def load(example):
    """load_series on the example text, via a temp file. -> (rules, None) or (None, error text)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "series.tsv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(example)
        try:
            return nested.load_series(p), None
        except ValueError as e:
            return None, str(e)


def game(name):
    g = ET.Element("game")
    ET.SubElement(g, "name").text = name
    return g


SAMPLE = {   # system -> {file: display name}
    "snes": {"a.sfc": "Gradius III", "b.sfc": "Super R-Type", "c.sfc": "Super Bomberman", "d.sfc": "Tetris Attack",
             "e.sfc": "Mega Man X"},
    "genesis": {"a.md": "Thunder Force IV", "b.md": "Mega Bomberman", "c.md": "Mega Man: The Wily Wars"},
    "nes": {"a.nes": "Mega Man 2", "b.nes": "Rockman 3", "c.nes": "Bomberman", "d.nes": "Tetris"},
}
WANT = {"my-shooters": 3, "bomberman": 3, "mega-man": 3}   # tetris has 2 games (< 3): no folder; Wily Wars: genesis


def built(rules):
    games = {s: {fn: game(n) for fn, n in files.items()} for s, files in SAMPLE.items()}
    return {slug: len(pairs) for slug, pairs in nested.build_series(rules, games).items()}


def launcher_systems():
    tree = ast.parse(read("skills/collections/launch.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CORE_BY_SYSTEM" for t in node.targets):
            return set(ast.literal_eval(node.value).keys())
    return set()


def prompt_problems(prompt, systems):
    probs = ["the AI prompt does not name system %s" % s for s in sorted(systems)
             if not re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(s), prompt)]
    if "TAB" not in prompt:
        probs.append("the AI prompt does not ask for TAB separators")
    if "at least 3" not in prompt:
        probs.append("the AI prompt does not say a folder needs at least 3 games")
    return probs


def link_problems(docs):
    return ["%s does not link LISTS.md" % rel for rel, text in docs.items() if "](LISTS.md)" not in text]


def words_problem(text):
    n = len(text.split())
    return ["%s: %d words > %d" % (LISTS, n, MAX_WORDS)] if n > MAX_WORDS else []


def test_example_is_a_real_series_file():
    example, _ = blocks(read(LISTS))
    rules, err = load(example)
    check(err is None and rules, "the LISTS.md example loads with load_series (%s)" % err)
    if rules:
        got = built(rules)
        check(got == WANT, "the example builds the folders the guide promises: %s" % got)
    check(load(example.replace("\t", "    "))[1] is not None, "BROKEN: spaces instead of tabs are refused")
    check(load(example + "bad\tBad\t(?i)gradius(\n")[1] is not None, "BROKEN: a bad regex is refused")
    check(load(example + "tetris\tTetris again\tTetris\n")[1] is not None, "BROKEN: a duplicate folder name is refused")


def test_prompt_names_every_system():
    _, prompt = blocks(read(LISTS))
    systems = launcher_systems()
    check(systems and prompt_problems(prompt, systems) == [], "the AI prompt %s" % prompt_problems(prompt, systems))
    check(prompt_problems(prompt.replace("atari2600", "atari"), systems) != [],
          "BROKEN: a prompt missing a system is reported")
    check(prompt_problems(prompt.replace("TAB", "tab"), systems) != [], "BROKEN: a prompt without TAB is reported")


def test_short_and_linked():
    text = read(LISTS)
    check(words_problem(text) == [], "%s is short %s" % (LISTS, words_problem(text)))
    check(words_problem(text + " word" * MAX_WORDS) != [], "BROKEN: a guide over %d words is reported" % MAX_WORDS)
    docs = {rel: read(rel) for rel in LINKED_FROM}
    check(link_problems(docs) == [], "START and FAQ link the guide %s" % link_problems(docs))
    docs["docs/FAQ.md"] = docs["docs/FAQ.md"].replace("](LISTS.md)", "](OTHER.md)")
    check(link_problems(docs) != [], "BROKEN: a missing link is reported")


if __name__ == "__main__":
    main(globals())
