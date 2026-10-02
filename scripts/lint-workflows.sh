#!/usr/bin/env bash
set -euo pipefail
curl -fsSL https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz -o /tmp/club-actionlint.tar.gz
echo '8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8  /tmp/club-actionlint.tar.gz' | sha256sum -c -
mkdir -p /tmp/club-workflow-lint
tar -xzf /tmp/club-actionlint.tar.gz -C /tmp/club-workflow-lint actionlint
/tmp/club-workflow-lint/actionlint
