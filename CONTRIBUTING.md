# Contributing

Thanks for helping. Run every command from the repo root; `make help` lists every target.

1. Fork the repo on GitHub and clone your fork.
2. `make config-venv` once if you have no `~/.venvs/coin-dojo` yet (the config loader needs PyYAML).
3. Make your change, with a test for it, including a broken case that must fail.
4. `make test` and `make lint`. A run passes when it exits 0 (its last line is `RESULT: PASS`).
5. Open a pull request against `main`.

Commit style: `type(scope): #N summary` (Conventional Commits, `#N` = the issue, at most 72 characters), for example
`fix(controls): #57 map the Saturn start button`.

Questions, ideas or a bug: open an issue on the repo.
