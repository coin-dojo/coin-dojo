# ansible/tests

## Run

    python3 ansible/tests/test_ansible_structure.py      # no deps
    pytest ansible/tests/                                 # if pytest is installed

Structure + secrets lint for the arcade-pack Ansible layer: required files, every role wired in
`site.yml`, no committed secrets, no committed game binaries, no hard tabs in YAML.
`test_no_orphan_roles.py` fails when a directory under `roles/` is used by no play (#110) and checks
`LIMIT=batocera` is refused. `test_secret_scanner_catches_planted_secret` is a built-in positive
control — it fails if the secret scanner ever stops catching a planted credential.

## The deliberately-broken case (must FAIL)

Prove the guard works. Either:

1. **Commit a password** — add to `inventory/hosts.yml`:

       ansible_ssh_pass: hunter2

   Re-run the test → `test_no_committed_secrets` and `test_inventory_has_no_password` go red.
   Remove the line to go green again.

2. **Drop a role** — delete `roles/dreamcast/tasks/main.yml` → `test_roles_have_tasks` and
   `test_site_wires_all_roles` go red.

3. **Commit a ROM** — `git add -f ansible/roles/dreamcast/files/sf2.zip` →
   `test_no_game_binaries` flags the committed binary.

4. **Leave a role unused** — `mkdir ansible/roles/old` → `test_no_orphan_roles.py` flags it.

Any of these turning the suite red is the intended behaviour: the lint is doing its job.

## Not covered here (needs ansible installed / real hardware)

`ansible-lint` and `ansible-playbook --syntax-check` are the next gate once Ansible is installed
(`pipx install ansible ansible-lint`). Playbook behaviour is proved by the dry run on the cabinet and by the test VM (`make vm-smoke`).
