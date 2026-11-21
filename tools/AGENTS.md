# tools/: rules for the tools

Commands and flags: `tools/README.md` and `make help`. Repo-wide rules: `../AGENTS.md`.

## Writing a tool

- Python 3.9+ stdlib only (the config loader's PyYAML lives in the `make config-venv` venv), or shell.
- Hosts, folders and Keychain item names come from config: python via `tools/hkconfig.py` (`get(name)`),
  shell via `tools/hkcfg NAME`. No constant pointing at anybody's host or home; an unset value stops with a message
  naming the variable. Test overrides stay env vars.
- Writes are dry runs unless `APPLY=1` (make) or an explicit apply verb.
- Output: data on stdout, `WARN`/`FAIL` lines and one final `RESULT: PASS|FAIL` last. Exit 0 ok, non-zero on failure.
- Never print a token or password; send it only in a header or on stdin.
- Make target names live in `make help`; a doc that names a different one is fixed, not the target.

## Testing a tool

- Tests in `tools/tests/` (area `tools`), throwaway git repos: no network, no real cabinet.
- A test that runs `tools/cilock.py` sets a scratch `CILOCK_DIR` (never the real host lock).

## Config, tests and the lock

| Piece | Home |
|---|---|
| Variables: type, default, range, consumers | `config/variables.d/<area>.yml` (one file per area; add one, then `make config-docs`) |
| Your values | `config/local.yml` (git-ignored; template `config/local.example.yml`) |
| Loader | `tools/config.py` (`make config-check`, `make config`, `make config-docs` writes `docs/CONFIG.md`) |
| Which tests a path needs | `ci/rules.tsv` (first match wins, last rule `* ALL`), `ci/areas.tsv`, `tools/ci_select.py` |
| Lock lanes | `tools/cilock.py`: heavy, light and cabinet lanes, so test and deploy runs on one machine take turns |
| Dry-run recap diff | `tools/recap.py A.log B.log`, `--zero B.log` |
