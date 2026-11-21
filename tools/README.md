# tools/ - the tools behind make

## config.py - the configuration loader

`tools/config.py check|show|get|render|guard-cmd|docs`: variables declared in `config/variables.d/*.yml`, overlaid by
`config/targets/<target>.yml`, your local file and the command line (`make config-check`, `make config`, `make config-docs`;
reference: `docs/CONFIG.md`). Needs PyYAML: `make config-venv` makes `~/.venvs/coin-dojo`; make and `bin/deploy` pick it up.
To add an area: one new `config/variables.d/<area>.yml`, then `make config-docs`; no shared file changes.

## Scripts read the config

The rule: `tools/AGENTS.md`. `tools/hkconfig.py` and `tools/hkcfg` both run `tools/config.py get --soft` with the config
venv's python, so the calling script needs no PyYAML.

## The other tools

| Command | What |
|---|---|
| `make test-file`, `make test-area`, `make test-changed`, `make ci` | `tools/ci_select.py` picks the tests a change needs (`ci/rules.tsv`, `ci/areas.tsv`), `tools/runtests.py` runs them, `tools/ci.py` adds lint and security |
| `tools/cilock.py run heavy\|cabinet\|light CMD` | lock lanes so test and deploy runs on one machine take turns (`make test` heavy, `make deploy*` cabinet, one test file light) |
| `tools/cpu-gate.sh` | waits until the Mac's CPU is below `CPU_MAX` (80) before a test VM step (`make vm-*`) |
| `make lint`, `make secrets` | `tools/lintfiles.py` lists the files each linter sees; `tools/secrets-scan.sh` runs gitleaks |
| `make doctor`, `make cabinet-status`, `make net-check` | `tools/doctor.sh`, `tools/cabinet-status.sh`, `tools/net-check.sh`: read-only checks of this Mac, the cabinet and its network |
| `make rollout` | `tools/rollout.sh`: dry run, apply, then a second dry run must show nothing changed (`tools/recap.py`) |
| `make versions`, `make bump` | `tools/versions.py`: every pinned third-party component in `versions.lock` |
| `make commit`, `make hooks` | `tools/commit.sh` builds a Conventional Commit (needs gum); the commit-msg hook runs `tools/commitlint.py check` |
| `make cache-migrate` | `tools/cache-migrate.sh`: moves old per-area cache folders under one cache root |
| `tools/changelog.py lint` | the `CHANGELOG.md` format check (Keep a Changelog 1.1.0; runs in `make test`) |

`make py-test` counts `SKIP` lines: `RESULT: PASS (N skipped)`.
