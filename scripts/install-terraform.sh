#!/usr/bin/env bash
# Install the pinned Terraform release after checking its SHA256. The zip and the
# provider plugin cache live in directories that the workflows restore with
# actions/cache; a restored zip is used only when its checksum still matches.
set -euo pipefail
VERSION=1.10.5
SHA256=0566a24f5332098b15716ebc394be503f4094acba5ba529bf5eb0698ed5e2a90
CACHE_DIR=/tmp/club-terraform
ZIP="$CACHE_DIR/terraform_${VERSION}_linux_amd64.zip"
PLUGIN_CACHE="$HOME/.terraform.d/plugin-cache"
mkdir -p "$CACHE_DIR" /tmp/terraform-bin "$PLUGIN_CACHE"
if ! echo "$SHA256  $ZIP" | sha256sum -c --status - 2>/dev/null; then
  # releases.hashicorp.com resets the connection often enough to fail a run
  # ("curl: (35) Recv failure"), and a dropped download is not a verification
  # result. Retry, then let the checksum decide whether what arrived is real.
  curl -fsSL --retry 5 --retry-all-errors --retry-delay 3 --connect-timeout 15 --max-time 180 \
    "https://releases.hashicorp.com/terraform/${VERSION}/terraform_${VERSION}_linux_amd64.zip" \
    -o "$ZIP"
fi
echo "$SHA256  $ZIP" | sha256sum -c -
unzip -o -q "$ZIP" -d /tmp/terraform-bin
echo /tmp/terraform-bin >> "$GITHUB_PATH"
# Terraform still checks every cached provider against .terraform.lock.hcl.
echo "TF_PLUGIN_CACHE_DIR=$PLUGIN_CACHE" >> "$GITHUB_ENV"
