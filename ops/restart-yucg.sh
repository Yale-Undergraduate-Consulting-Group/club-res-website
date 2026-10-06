#!/bin/bash
# Deploy one verified image digest to this box. The ship action sends this file
# through SSM AWS-RunShellScript after envsubst renders only the IMAGE, ECR_HOST,
# DEPLOYMENT_ENV and OPS_BUNDLE placeholders. Output reaches GitHub logs: never
# print secrets.
set -euo pipefail
umask 0077
IMAGE='${IMAGE}'
ECR_HOST='${ECR_HOST}'
DEPLOYMENT_ENV='${DEPLOYMENT_ENV}'
[[ "$ECR_HOST" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com$ ]] || { echo 'ECR registry host required'; exit 1; }
[[ "$IMAGE" =~ @sha256:[a-f0-9]{64}$ ]] && [ "${IMAGE%%/*}" = "$ECR_HOST" ] \
  || { echo 'Immutable image digest in this registry required'; exit 1; }
case "$DEPLOYMENT_ENV" in
  prod) APP_ENV=production ;;
  # Dev neither sends mail nor synchronizes real Gmail accounts in background jobs.
  dev) APP_ENV=beta ;;
  *) echo 'DEPLOYMENT_ENV must be dev or prod'; exit 1 ;;
esac
mountpoint -q /data || { echo 'Retained /data volume is not mounted; refusing deployment'; exit 1; }
exec 9>/var/lock/yucg-deploy.lock
flock -n 9 || { echo 'Deployment already running'; exit 1; }
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK" /etc/yucg/config.json.new /etc/yucg/app.env.new' EXIT
# Box scripts from this revision's ops/ (base64 tar.gz); installed after a healthy release.
base64 -d <<< '${OPS_BUNDLE}' | tar -xzf - -C "$WORK"
for file in backup_sqlite.py backup-to-s3.sh yucg-backup.service yucg-backup.timer; do
  [ -s "$WORK/$file" ] || { echo "Ops bundle lacks $file"; exit 1; }
done

# Refresh the non-secret box configuration from its SSM parameter (Terraform
# writes both), so configuration changes never require editing the box.
CONFIG=/etc/yucg/config.json
valid_config() {
  jq -e '(.email_delivery_enabled | type == "boolean") and (.edge | type == "boolean")
    and (.public_url | type == "string" and startswith("http"))
    and ([.env, .region, .secret_arn, .catalog_bucket, .documents_bucket, .backups_bucket, .log_group, .bedrock_model_id, .config_parameter]
         | all(type == "string" and length > 0))' "$1" >/dev/null
}
REGION="$(jq -er .region "$CONFIG")"
PARAMETER="$(jq -er .config_parameter "$CONFIG")"
aws ssm get-parameter --region "$REGION" --name "$PARAMETER" --query Parameter.Value --output text > "$CONFIG.new"
valid_config "$CONFIG.new" || { echo "SSM parameter $PARAMETER is not a complete box configuration"; exit 1; }
mv -f "$CONFIG.new" "$CONFIG"
cfg() { jq -er --arg key "$1" '.[$key] | tostring' "$CONFIG"; }
[ "$(cfg env)" = "$DEPLOYMENT_ENV" ] || { echo 'Box configuration belongs to another environment'; exit 1; }
REGION="$(cfg region)"
LOG_GROUP="$(cfg log_group)"
PUBLIC_URL="$(cfg public_url)"
PUBLIC_URL="${PUBLIC_URL%/}"
EMAIL_DELIVERY_ENABLED="$(cfg email_delivery_enabled)"
if [ "$DEPLOYMENT_ENV" = dev ] && [ "$EMAIL_DELIVERY_ENABLED" != false ]; then
  echo 'Dev must never deliver real email'; exit 1
fi
# The listener must match the security group: only an edge box accepts CloudFront
# VPC-origin traffic on :80. Without edge, reach it through SSM port forwarding.
if [ "$(cfg edge)" = true ]; then
  PUBLISH=80:8000 HEALTH_URL=http://127.0.0.1/api/health
else
  PUBLISH=127.0.0.1:8000:8000 HEALTH_URL=http://127.0.0.1:8000/api/health
fi

command -v docker-credential-ecr-login >/dev/null || dnf install -y -q amazon-ecr-credential-helper
export DOCKER_CONFIG="$WORK/docker" AWS_ECR_DISABLE_CACHE=true
install -d -m 0700 "$DOCKER_CONFIG"
printf '{"credHelpers":{"%s":"ecr-login"}}\n' "$ECR_HOST" > "$DOCKER_CONFIG/config.json"
docker pull --quiet "$IMAGE"

# Render the container environment from Secrets Manager plus non-secret
# configuration on every deploy. Fixed configuration lines come last and win.
SECRET_JSON="$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$(cfg secret_arn)" --query SecretString --output text)"
printf '%s' "$SECRET_JSON" | jq -e 'type == "object"
  and (.JWT_SECRET | type == "string" and length >= 32)
  and all(keys[]; test("^[A-Z][A-Z0-9_]*$"))
  and all(.[]; . == null or (type == "string" and (test("[\r\n]") | not)))' >/dev/null \
  || { echo 'App secret must be a JSON object of single-line strings including JWT_SECRET'; exit 1; }
install -d -m 0700 /etc/yucg
{
  printf '%s' "$SECRET_JSON" | jq -r 'to_entries[] | select(.value != null) | "\(.key)=\(.value)"'
  unset SECRET_JSON
  cat <<ENV
APP_ENV=$APP_ENV
EMAIL_DELIVERY_ENABLED=$EMAIL_DELIVERY_ENABLED
DATABASE_URL=sqlite:////data/clientreach.db
LLM_PROVIDER=bedrock
BEDROCK_MODEL_ID=$(cfg bedrock_model_id)
BEDROCK_RANK_MODEL_ID=$(cfg bedrock_model_id)
BEDROCK_ALLOWED_MODEL_IDS=$(cfg bedrock_model_id)
BEDROCK_CALLS_PER_CLUB_PER_HOUR=200
ASSISTANT_REQUESTS_PER_MEMBER_PER_HOUR=15
ASSISTANT_REQUESTS_PER_CLUB_PER_HOUR=120
BEDROCK_INPUT_USD_PER_MILLION=1.00
BEDROCK_OUTPUT_USD_PER_MILLION=5.00
AI_REVIEW_WORKERS=2
INBOX_VERIFY_MODE=mx
CATALOG_BUCKET=$(cfg catalog_bucket)
DOCUMENTS_BUCKET=$(cfg documents_bucket)
AWS_REGION=$REGION
AWS_DEFAULT_REGION=$REGION
FRONTEND_DIST=/app/frontend_dist
FRONTEND_URL=$PUBLIC_URL
BACKEND_URL=$PUBLIC_URL
CORS_ORIGINS=$PUBLIC_URL
GOOGLE_REDIRECT_URI=$PUBLIC_URL/api/auth/google/callback
ENV
} > /etc/yucg/app.env.new
[ ! -f /etc/yucg/app.env ] || cp -p /etc/yucg/app.env "$WORK/previous.env"
mv -f /etc/yucg/app.env.new /etc/yucg/app.env

OLD_IMAGE=""
if docker container inspect yucg >/dev/null 2>&1; then
  OLD_IMAGE="$(docker inspect --format '{{.Image}}' yucg)"
  OLD_MOUNT="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' yucg)"
  [ "$OLD_MOUNT" = /data ] || { echo 'Unexpected current database mount'; exit 1; }
  [ -f /data/clientreach.db ] || { echo 'Running application has no /data/clientreach.db; refusing deployment'; exit 1; }
fi
BACKUP=""
if [ -f /data/clientreach.db ]; then
  # SQLite online backup accounts for WAL; never a raw copy of a live database.
  install -d -m 0700 /data/backups
  BACKUP="/data/backups/predeploy-$(date -u +%Y%m%dT%H%M%SZ).db"
  BACKUP="$BACKUP" python3 -c 'import os,sqlite3; s=sqlite3.connect("file:/data/clientreach.db?mode=ro",uri=True); d=sqlite3.connect(os.environ["BACKUP"]); s.backup(d); assert d.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; d.close(); s.close()'
else
  echo 'First deployment: the application initializes an empty database on /data'
fi

# Probe with the candidate image identity before stopping the running application.
docker run --rm --network none --entrypoint python -v /data:/data "$IMAGE" -c 'import os,sqlite3; assert os.geteuid()!=0,"Non-root image required"; assert os.access("/data",os.W_OK),"Review /data ownership for UID 10001 before deploying"; db="/data/clientreach.db"; c=sqlite3.connect("file:"+db+"?mode=rw",uri=True) if os.path.exists(db) else None; c and (c.execute("BEGIN IMMEDIATE"), c.rollback(), c.close())'
run_image() {
  docker rm -f yucg >/dev/null 2>&1 || true
  docker run -d --name yucg --restart unless-stopped --env-file /etc/yucg/app.env \
    -p "$PUBLISH" -v /data:/data --log-driver=awslogs \
    --log-opt awslogs-region="$REGION" --log-opt awslogs-group="$LOG_GROUP" \
    --log-opt awslogs-stream=api "$1" >/dev/null
}
healthy() {
  for _ in $(seq 1 30); do
    if curl --fail --silent --max-time 3 "$HEALTH_URL" >/dev/null && \
       docker exec yucg python -c 'import sqlite3; c=sqlite3.connect("file:/data/clientreach.db?mode=rw",uri=True); assert c.execute("PRAGMA quick_check").fetchone()[0]=="ok"; c.execute("SELECT count(*) FROM users"); c.close()' 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}
if ! run_image "$IMAGE" || ! healthy; then
  echo "Release failed; application logs are in CloudWatch $LOG_GROUP. Database is not automatically rolled back."
  if [ -z "$OLD_IMAGE" ]; then
    docker rm -f yucg >/dev/null 2>&1 || true
    exit 1
  fi
  echo 'Restoring the previous image and environment.'
  [ ! -f "$WORK/previous.env" ] || cp -p "$WORK/previous.env" /etc/yucg/app.env
  run_image "$OLD_IMAGE"
  healthy || { echo 'CRITICAL: previous image also unhealthy; manual restore review required'; exit 2; }
  exit 1
fi

# Daily off-box SQLite backup to the backups bucket, refreshed on every deploy.
install -d -m 0755 /opt/yucg
install -m 0700 "$WORK/backup_sqlite.py" "$WORK/backup-to-s3.sh" /opt/yucg/
install -m 0644 "$WORK/yucg-backup.service" "$WORK/yucg-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now yucg-backup.timer

# Each release leaves the previous ~455 MB image behind and filled the 20 GB
# root disk in the old stack. Keep only the serving image and its rollback target.
NEW_ID="$(docker inspect --format '{{.Id}}' "$IMAGE")"
docker images -q --no-trunc | sort -u | grep -vx -e "$NEW_ID" -e "${OLD_IMAGE:-none}" \
  | xargs -r docker rmi -f >/dev/null 2>&1 || true
# Pre-deploy snapshots are full database copies on the 8 GB data volume. Keep the
# five newest, only after a healthy release; the S3 backups are the long-term record.
ls -1t /data/backups/predeploy-*.db 2>/dev/null | tail -n +6 | xargs -r rm -f || true
echo "Healthy release of $IMAGE in $DEPLOYMENT_ENV${BACKUP:+; pre-deploy backup $BACKUP}"
