#!/usr/bin/env bash
# Seed the container secret club-res-website/<env>/app created by Terraform.
# Operator credentials, never CI. Values never reach Terraform state, argv or
# the terminal. JWT_SECRET is generated once and kept on later runs (rotating
# it signs every member out); set ROTATE_JWT_SECRET=1 to replace it.
#
# Each other key is taken from the environment variable of the same name, else
# read silently from stdin (one line per key, in the order below). An empty
# answer keeps the current value, or stores "" when there is none.
#
#   AWS_PROFILE=yucg-verse scripts/seed-secrets.sh dev
set -euo pipefail
umask 0077

ENV_NAME="${1:?usage: seed-secrets.sh <dev|prod>}"
[[ "$ENV_NAME" == dev || "$ENV_NAME" == prod ]] || { echo 'environment must be dev or prod' >&2; exit 1; }
REGION="${AWS_REGION:-us-east-2}"
SECRET_ID="club-res-website/$ENV_NAME/app"
KEYS=(GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET APIFY_API_TOKEN TAVILY_API_KEY SLACK_CLIENT_ID
  SLACK_CLIENT_SECRET SLACK_SIGNING_SECRET SLACK_BOT_TOKEN VERIFALIA_API_KEY)

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
echo "Seeding $SECRET_ID in account $ACCOUNT, region $REGION" >&2

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
# A secret with no version yet starts empty; any other failure stops here.
if ! aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" \
  --query SecretString --output text > "$WORK/current.json" 2> "$WORK/error.log"; then
  grep -q ResourceNotFoundException "$WORK/error.log" || { cat "$WORK/error.log" >&2; exit 1; }
  echo '{}' > "$WORK/current.json"
fi
jq -e 'type == "object"' "$WORK/current.json" >/dev/null

cp "$WORK/current.json" "$WORK/next.json"
set_key() { # $1 key, $2 value; the value travels through a file, not argv
  printf '%s' "$2" > "$WORK/value"
  jq --arg key "$1" --rawfile value "$WORK/value" '.[$key] = $value' "$WORK/next.json" > "$WORK/tmp.json"
  mv "$WORK/tmp.json" "$WORK/next.json"
}

if [ "${ROTATE_JWT_SECRET:-0}" = 1 ] || ! jq -e '(.JWT_SECRET // "") | length >= 32' "$WORK/next.json" >/dev/null; then
  set_key JWT_SECRET "$(openssl rand -hex 32)"
fi

for key in "${KEYS[@]}"; do
  value="${!key:-}"
  if [ -z "$value" ]; then
    [ -t 0 ] && printf '%s (empty keeps current): ' "$key" >&2
    IFS= read -rs value || value=''
    [ -t 0 ] && echo >&2
  fi
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || { echo "$key must be a single line" >&2; exit 1; }
  if [ -n "$value" ]; then
    set_key "$key" "$value"
  elif ! jq -e --arg key "$key" 'has($key)' "$WORK/next.json" >/dev/null; then
    set_key "$key" ''
  fi
done

aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_ID" \
  --secret-string "file://$WORK/next.json" --query VersionId --output text >/dev/null
echo "Stored keys: $(jq -r 'keys | join(", ")' "$WORK/next.json")" >&2
