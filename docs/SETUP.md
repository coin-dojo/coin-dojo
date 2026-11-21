# Setup: from a fresh Mac to a deployed cabinet

You need: an Apple Silicon Mac with Python 3.9+, Docker through Colima (`colima start`), and a cabinet running
Debian 13 arm64 that you can reach over SSH with a key. Optional: `gum` (for `make commit`), `limactl` (test VM).
Run every command from the repo root. Building a new SD card image instead: [BUILD.md](BUILD.md).

## Set up the Mac

1. `make setup`: makes the venv `~/.venvs/coin-dojo`, installs the commit hook and copies
   `config/local.example.yml` to `config/local.yml` (an existing one is left as is).
2. Edit `config/local.yml`. Set at least `hk_cabinet_host`; check `hk_cabinet_user`, `hk_cabinet_uid` (`id -u` on
   the cabinet) and `hk_ssh_key`. Every variable: [CONFIG.md](CONFIG.md).
3. Store the cabinet's sudo password in your Keychain and put the item's **name** in `hk_become_keychain`:
   `security add-generic-password -s <item name> -a <cabinet user> -w` (it prompts; never put the password in a file).
4. `make doctor`. PASS = the last line is `RESULT: PASS doctor`. Fix each FAIL line as it says, then run it again.
5. `make config-check`. PASS = `config-check: OK`. `make config` shows every value and where it comes from.

## Use your own art (optional)

6. Put a 1440x1080 PNG at `<your folder>/backdrops/default.png` (a `<system>.png` next to it is optional) and set
   `hk_theme_art_dir: <your folder>`. Without it the theme shows its own community backdrop.
7. `make preview` renders the theme on the Mac (screenshots under `~/.cache/coin-dojo/preview/out`).

## Deploy

8. `make deploy BECOME=1`: a dry run. Read the diff; nothing is written.
9. `make rollout APPLY=1 BECOME=1`: applies, runs a second dry run that must recap `changed=0`, then
   `make esde-verify`. PASS = the last line is `RESULT: PASS rollout`. Logs: `~/.cache/coin-dojo/rollout/`.
10. `make cabinet-status`: ES-DE up, no game running, page size 4096, free space, zram, deployed REV = your checkout.

## Try it in the test VM first (optional)

11. `make vm-up`, then `make vm-provision` and `make vm-smoke`. Always finish with `make vm-down`.
    Details: [vm/README.md](../vm/README.md).

## When something goes wrong

| Problem | Go to |
|---|---|
| Any make target or flag | `make help` |
