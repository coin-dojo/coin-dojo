# Ansible control node for the coin-dojo cabinet deploy (ticket #90; pattern from the maintainer's other repo's
# ansible/bin/ans). The laptop needs only Docker (Colima); Ansible is never installed on the host.
# Every version is pinned here or in requirements.txt / requirements.yml. bin/ans tags the image
# with a hash of these three files, so a pin change rebuilds on next use.
# Lint + secrets tools (#124): static binaries copied from upstream images pinned by digest.
FROM koalaman/shellcheck-alpine:v0.10.0@sha256:5921d946dac740cbeec2fb1c898747b6105e585130cc7f0602eec9a10f7ddb63 AS shellcheck
FROM zricethezav/gitleaks:v8.21.2@sha256:0e99e8821643ea5b235718642b93bb32486af9c8162c8b8731f7cbdc951a7f46 AS gitleaks

FROM python:3.13.2-slim-bookworm@sha256:6b3223eb4d93718828223966ad316909c39813dee3ee9395204940500792b740

# openssh-client: the cabinet connection; rsync: ansible.posix.synchronize (control-node side);
# sshpass: only when bin/deploy passes a cabinet password instead of a key.
RUN apt-get -o Acquire::Retries=5 update \
 && apt-get -o Acquire::Retries=5 install -y --no-install-recommends openssh-client rsync sshpass ca-certificates git \
 && rm -rf /var/lib/apt/lists/*

COPY --from=shellcheck /bin/shellcheck /usr/local/bin/shellcheck
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
RUN git config --system --add safe.directory '*'

COPY requirements.txt requirements.yml /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && ansible-galaxy collection install -r /tmp/requirements.yml -p /usr/share/ansible/collections \
 && shellcheck --version >/dev/null && gitleaks version >/dev/null && ansible-lint --version >/dev/null \
 && yamllint --version >/dev/null && ruff --version >/dev/null
