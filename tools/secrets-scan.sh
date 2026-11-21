#!/bin/sh
# Secrets scan (ticket #124): runs INSIDE the pinned Ansible image (`make secrets`).
#   secrets-scan.sh [REPO]   REPO = a checkout (default .); its .gitleaks.toml is the config
# 1. history: gitleaks on a bare clone made here with the transport (--no-local). On the Mac, sandboxed processes
#    leave com.apple.provenance on .git/objects files and Colima's mount shows them as AppleDouble `._pack-*`, which
#    git rejects ("index file is too small"); the clone carries real objects only. A clone failure FAILS the scan.
#    Scope: HEAD's history; GITLEAKS_ALL_REFS=1 = every ref (the laptop repo holds every agent worktree's branch).
# 2. the working copy, uncommitted files included.
set -u
repo=${1:-.}
cfg=$repo/.gitleaks.toml
[ -f "$cfg" ] || { echo "secrets-scan: no $cfg" >&2; exit 2; }
t=$(mktemp -d) || exit 1
trap 'rm -rf "$t"' EXIT
rc=0
if git clone --quiet --bare --no-local "$repo" "$t/repo.git"; then
  if [ "${GITLEAKS_ALL_REFS:-}" = 1 ]; then
    gitleaks git --no-banner --redact --exit-code 1 --verbose --config "$cfg" "$t/repo.git" || rc=1
  else
    gitleaks git --no-banner --redact --exit-code 1 --verbose --config "$cfg" --log-opts="--full-history HEAD" "$t/repo.git" || rc=1
  fi
else
  echo "secrets-scan: git clone --bare failed" >&2; rc=1
fi
gitleaks dir --no-banner --redact --exit-code 1 --verbose --config "$cfg" "$repo" || rc=1
exit "$rc"
