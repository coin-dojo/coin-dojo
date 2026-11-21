# Working conventions (Coin Dojo)

Rules for anyone working in this repo, people and AI agents alike. Start with `README.md` and `CONTRIBUTING.md`,
then the `AGENTS.md` of the folder you change (`ansible/`, `skills/`, `tools/`). `make help` lists every target and
flag and wins over any doc.

## Secrets

- No secrets in git, ever: no SSH private keys, passwords, tokens or API keys.
- Config holds only *which* Keychain item or environment variable to read (`hk_*` variables, `docs/CONFIG.md`);
  scripts read the value at run time and never echo it.
- Your own values (cabinet host, Keychain names) go in `config/local.yml` (git-ignored), never in a shared file.
  `tests/test_no_local_values.py` fails on them.

## Ship no artifacts

- The project ships only the method: automation and instructions for *your own* cabinet.
- Never in git: Pi images, launcher or emulator binaries, ROMs, BIOS, firmware, disk images, secrets. The only art
  that ships is credited in `THIRD_PARTY.md`. Third-party software is installed from upstream, never redistributed.
- Licences: MIT for code (`LICENSE`), CC BY-SA 4.0 for original art and docs (`LICENSE-art`), fonts keep theirs
  (`NOTICE`). Gate: `tests/test_ship_no_artifacts.py`.

## Back up and dry run

- Back up the cabinet's SD card before writing an image to it, and verify an image before writing it to hardware.
- Cabinet and host targets are dry runs unless `APPLY=1`. Read the dry run first. Cabinet rules: `ansible/AGENTS.md`.
- Try risky changes in the test VM (`vm/README.md`) or the local preview (`skills/local-preview/`) first.

## Tests

- A change to a script or config ships its tests in the same change, including a **broken case** that must FAIL.
- Tests sit next to the code (`<area>/tests/`, `skills/<skill>/tests/`); repo-wide gates in `tests/`. Plain
  `python3 <file>` with `tests/lib/hktest.py`, no pytest needed.
- A run's verdict is its exit code or its **last** `RESULT:` line.
- Every test file belongs to an area in `ci/areas.tsv`; a new top-level path needs a rule in `ci/rules.tsv`.
- Iterate with `make test-file F=path`, then `make test-changed`; `make lint` and `make test` before a pull request.

## Commits and the changelog

- Subject `type(scope): #N summary` (at most 72 characters; `#N` is the issue number), checked by `make hooks`.
- A change you can see or feel on the cabinet adds one bullet under `## [Unreleased]` in `CHANGELOG.md`.
