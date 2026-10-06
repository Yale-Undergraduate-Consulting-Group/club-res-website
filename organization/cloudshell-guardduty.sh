#!/usr/bin/env bash
# Run in AWS CloudShell in the Organizations MANAGEMENT account.
# Turns on Amazon GuardDuty in us-east-2 and makes it cover every member account,
# including ones created later. The management account is its own GuardDuty
# administrator. GuardDuty bills per account on usage after a 30-day free trial.
# Safe to run again.
set -euo pipefail

REGION=us-east-2
export AWS_DEFAULT_REGION="$REGION"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
[ "$(aws organizations describe-organization --query Organization.MasterAccountId --output text)" = "$ACCOUNT" ] \
  || { echo "Run this in the management account." >&2; exit 1; }

DETECTOR="$(aws guardduty list-detectors --query 'DetectorIds[0]' --output text)"
if [ "$DETECTOR" = None ]; then
  DETECTOR="$(aws guardduty create-detector --enable --finding-publishing-frequency SIX_HOURS --query DetectorId --output text)"
  echo "detector $DETECTOR created"
else
  echo "detector $DETECTOR exists"
fi

aws organizations enable-aws-service-access --service-principal guardduty.amazonaws.com
ADMIN="$(aws guardduty list-organization-admin-accounts --query 'AdminAccounts[0].AdminAccountId' --output text)"
if [ "$ADMIN" = None ]; then
  aws guardduty enable-organization-admin-account --admin-account-id "$ACCOUNT"
  echo "administrator set to $ACCOUNT"
else
  echo "administrator is already $ADMIN"
fi
aws guardduty update-organization-configuration --detector-id "$DETECTOR" --auto-enable-organization-members ALL
echo "GuardDuty will enable itself in every member account in $REGION."
