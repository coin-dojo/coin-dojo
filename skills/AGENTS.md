# skills/: agent rules

The list of skills: `skills/README.md`. Repo-wide rules: `../AGENTS.md`.

## A skill is deterministic

- One task per skill. If it branches a lot, split it.
- Numbered, copy-pasteable steps with exact commands and real paths; no "figure out the right flag".
- A verify gate after every step that changes state: the exact check, and what PASS and FAIL look like. Stop on FAIL.
- Dry run or preview before any destructive step (`dd`, partition edits, deleting files, cabinet writes).
- Same inputs, same steps. If a step depends on something, name it and give each branch.
- No secrets: name where a secret lives (Keychain item, config variable), never its value.
- Written so a local LLM or a human can follow it without improvising.

## Layout

- `skills/<name>/SKILL.md` is the workflow. Its code sits in the same folder (`scripts/` is optional), its tests in
  `skills/<name>/tests/`, reachable from an area in `ci/areas.tsv` (an orphan test FAILs the selector test).
- A script ships with a test, including a broken case that must FAIL, in the same branch.
- Values a role or another tool also needs come from config (`hk_*`, `config/variables.d/`), not a second literal.
- A new skill folder gets its `owners.tsv` line in the same commit, and a `ci/rules.tsv` rule (without one,
  `skills/*` runs every area).

## Hot folders

- `skills/controls` (more than 10 commits a day): one agent at a time.
