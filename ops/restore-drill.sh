#!/bin/bash
# Restore drill: prove last night's off-box backup restores and boots the app,
# without touching the live database. Run on the box as root, e.g. through
# `aws ssm send-command ... AWS-RunShellScript` with this file as the command.
# It downloads the newest complete backup set to a scratch directory, checks
# its SHA-256 against the manifest and runs PRAGMA integrity_check, compares
# row counts with the live database (read-only), then boots the serving image
# on the copy with networking disabled, so nothing can be sent. Everything it
# creates is removed on exit. Prints timings for docs/CI_CD.md.
set -euo pipefail
B=$(jq -er .backups_bucket /etc/yucg/config.json)
R=$(jq -er .region /etc/yucg/config.json)
D=$(mktemp -d /var/tmp/yucg-restore-drill.XXXX)
chmod 700 "$D"
trap 'docker rm -f yucg-restore-drill >/dev/null 2>&1 || true; rm -rf -- "$D"' EXIT
t() { date +%s.%N; }
T0=$(t)
KEY=$(aws s3api list-objects-v2 --bucket "$B" --region "$R" --prefix sqlite/ \
  --query 'sort_by(Contents[?ends_with(Key, `manifest.json`)], &LastModified)[-1].Key' --output text)
PREFIX=${KEY%/manifest.json}
echo "latest backup set: ${PREFIX#sqlite/}"
aws s3 cp "s3://$B/$PREFIX/manifest.json" "$D/manifest.json" --region "$R" --only-show-errors
T1=$(t)
aws s3 cp "s3://$B/$PREFIX/snapshot.db" "$D/snapshot.db" --region "$R" --only-show-errors
T2=$(t)
chown 10001:10001 "$D" "$D/snapshot.db"
python3 - "$D" <<'PY'
import hashlib, json, sqlite3, sys
d = sys.argv[1]
m = json.load(open(d + '/manifest.json'))
h = hashlib.sha256(open(d + '/snapshot.db', 'rb').read()).hexdigest()
assert h == m['sha256'], 'checksum does not match the manifest'
c = sqlite3.connect('file:' + d + '/snapshot.db?mode=ro', uri=True)
assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'integrity check failed'
live = sqlite3.connect('file:/data/clientreach.db?mode=ro', uri=True)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
count = lambda db, t: db.execute('select count(*) from "' + t.replace('"', '""') + '"').fetchone()[0]
same = sum(count(c, t) == count(live, t) for t in tables)
print(f"{m['bytes']} bytes, checksum and integrity ok; {same} of {len(tables)} tables match live row counts")
PY
T3=$(t)
IMAGE=$(docker inspect --format '{{.Image}}' "yucg-$(cat /etc/yucg/active-slot)")
docker run -d --name yucg-restore-drill --network none --memory 1g -v "$D:/drill" \
  -e DATABASE_URL=sqlite:////drill/snapshot.db -e JWT_SECRET=restore-drill-only-not-a-real-secret-0123456789 \
  "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
  if docker exec yucg-restore-drill python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" 2>/dev/null; then
    echo "restored app answers /api/health"
    break
  fi
  sleep 2
done
T4=$(t)
python3 -c "import sys; a=[float(x) for x in sys.argv[1:]]; print(f'timing: download {a[2]-a[1]:.1f}s, verify {a[3]-a[2]:.1f}s, boot {a[4]-a[3]:.1f}s, total {a[4]-a[0]:.1f}s')" "$T0" "$T1" "$T2" "$T3" "$T4"
