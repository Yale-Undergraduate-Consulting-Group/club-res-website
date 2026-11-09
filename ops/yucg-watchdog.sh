#!/bin/bash
# Restart the serving container when Docker reports it unhealthy (yucg-watchdog.timer).
# Docker's restart policy covers a process that exits; this covers one that is
# still running but no longer answers /api/health. It holds the deploy lock, so
# it never acts during a release, and a release never starts mid-restart.
set -euo pipefail
exec 9>/var/lock/yucg-deploy.lock
flock -n 9 || exit 0
SLOT="$(cat /etc/yucg/active-slot 2>/dev/null || true)"
[ -n "$SLOT" ] || exit 0
NAME="yucg-$SLOT"
STATUS="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$NAME" 2>/dev/null || true)"
[ "$STATUS" = unhealthy ] || exit 0
echo "$NAME is unhealthy; restarting it"
docker restart -t 30 "$NAME" >/dev/null
echo "$NAME restarted"
