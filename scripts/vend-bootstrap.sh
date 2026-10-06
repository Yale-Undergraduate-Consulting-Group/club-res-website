#!/usr/bin/env bash
# Run scripts/bootstrap-account.sh inside a member account vended by organization/.
# Caller: management-account credentials allowed sts:AssumeRole on the member's
# OrganizationAccountAccessRole (organization.yml apply job, or an operator).
# The 15-minute member credentials live only in this process's environment and the
# bootstrap child; nothing is written to disk or printed.
#
#   scripts/vend-bootstrap.sh 123456789012 us-east-2
set -euo pipefail
ACCOUNT="${1:?usage: vend-bootstrap.sh <account-id> <region>}"
REGION="${2:?usage: vend-bootstrap.sh <account-id> <region>}"
[[ "$ACCOUNT" =~ ^[0-9]{12}$ ]] || { echo "account id must be 12 digits" >&2; exit 1; }
[[ "$REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || { echo "invalid region" >&2; exit 1; }
ROLE="arn:aws:iam::${ACCOUNT}:role/OrganizationAccountAccessRole"

# A just-created account's access role can take a few minutes to become assumable.
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if CREDS="$(aws sts assume-role --role-arn "$ROLE" --role-session-name yucg-vend \
    --duration-seconds 900 --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
    --output text)"; then
    break
  fi
  [ "$attempt" -lt 10 ] || { echo "cannot assume $ROLE" >&2; exit 1; }
  sleep 30
done

(
  # Parameter expansion, not a here-string: older bash backs here-strings with a temp file.
  TAB=$'\t'
  AWS_ACCESS_KEY_ID="${CREDS%%"$TAB"*}"
  REST="${CREDS#*"$TAB"}"
  AWS_SECRET_ACCESS_KEY="${REST%%"$TAB"*}"
  AWS_SESSION_TOKEN="${REST#*"$TAB"}"
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  unset AWS_PROFILE
  export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
  test "$(aws sts get-caller-identity --query Account --output text)" = "$ACCOUNT" \
    || { echo "assumed credentials are not for account $ACCOUNT" >&2; exit 1; }
  exec bash "$(dirname "$0")/bootstrap-account.sh" "$REGION"
)
