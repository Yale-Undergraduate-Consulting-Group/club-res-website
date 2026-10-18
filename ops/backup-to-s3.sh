#!/bin/bash
# Daily verified SQLite snapshot to the private backups bucket (yucg-backup.timer).
# The instance role supplies credentials; the bucket's default CMK encrypts objects.
set -euo pipefail
SOURCE="${1:?Pass explicit SQLite source path}"
BUCKET="$(jq -er .backups_bucket /etc/yucg/config.json)"
REGION="$(jq -er .region /etc/yucg/config.json)"
[[ "$BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT
python3 "$SCRIPT_DIR/backup_sqlite.py" "$SOURCE" --destination "$WORK/snapshot.db" > "$WORK/manifest.json"
# Rehearse the completed online snapshot before uploading it.
python3 "$SCRIPT_DIR/backup_sqlite.py" "$WORK/snapshot.db" > "$WORK/restore-check.json"
KEY="sqlite/$(date -u +%Y/%m/%d/%Y%m%dT%H%M%SZ)-$(basename "$WORK")"
for file in snapshot.db restore-check.json manifest.json; do
  # Manifest last marks a complete backup set. Retention is the bucket lifecycle.
  aws s3 cp "$WORK/$file" "s3://${BUCKET}/${KEY}/$file" --region "$REGION" --only-show-errors
done
