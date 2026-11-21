# ansible/: agent rules (deploy, cabinet, test VM)

Reference (roles, flags, flows): `ansible/README.md`, `vm/README.md`. Repo-wide rules: `../AGENTS.md`.
Owner: Platform (`ansible/**`), except `roles/power` (Input) and `roles/settings` (Design).

## Deploys

- Ansible runs only through make or `bin/deploy` / `bin/ans` (pinned container). No Ansible on the Mac.
- Every write is a dry run first: `make deploy [TAGS=...] [BECOME=1]`, read the diff, then `APPLY=1`.
- Only Coordinator applies to the cabinet. Agents run dry runs and the test VM.
- A run is limited to one host: the cabinet (`coin-dojo`) or `LIMIT=coin-dojo-vm`; any other value is refused.
- `make config-check` runs before every deploy; a failing config stops it.
- After an apply, the next dry run must recap `changed=0`. Compare two dry runs with `tools/recap.py A.log B.log`;
  `tools/recap.py --zero B.log` checks `changed=0`. Save a log with `make deploy 2>&1 | tee A.log`.
- A refactor that must not change the cabinet proves it: dry-run recap identical before and after.
- The cabinet targets (`make deploy*`, `make smoke-launch`) take the cabinet lane of `tools/cilock.py` themselves:
  run them plain, never inside `cilock.py run heavy`.

## Cabinet safety

- Never write to the cabinet while a game runs: `make cabinet-guard`; `make deploy*` with `APPLY=1` runs it first.
- Restart ES-DE only through make (the ES-DE restart target in `make help`, `APPLY=1`) or the role handler
  `restart es-de`. `pkill -x cage` restarts the whole session; quick repeated restarts trip the autologin limit.
- Do not play a game between `pull-gamelists` and `deploy-collections W=`: ES-DE rewrites gamelists when a game is
  played, and the drift check then aborts.
- Theme deploys never copy the whole theme folder (`../AGENTS.md`, "Deploying the theme").
- Unattended sudo: `bin/deploy` reads the Keychain item named in config (or `COIN_DOJO_BECOME_PASS`) and passes it in
  a private vars file. Never echo it, put it in argv, a file or a commit. Your own sudo: `sudo -k -S -p ""` with the
  secret on stdin from the Keychain.

## Roles

- Levers are config variables (`config/variables.d/<area>.yml`, `hk_*`). A role default is a pass-through to one
  (`x: "{{ hk_x }}"`) or marked `# internal`; a literal knob in a default FAILs the role tests.
- Every role under `roles/` is used by `site.yml` or `vm.yml` (`tests/test_no_orphan_roles.py`); no two roles write
  the same file (`tests/test_no_duplicate_dest.py`).
- Build outputs (`build/*/out/`) are sha256-pinned in role defaults and never committed.

## Test VM

- Prove risky cabinet changes on `coin-dojo-vm` first: `make vm-up`, `make vm-provision`, `make vm-smoke`.
- Always `make vm-down` when done. One heavy step at a time (CPU gate: `tools/cpu-gate.sh`).
