#!/usr/bin/env bash
# One-time operator bootstrap for a YUCG AWS account (CI_CD.md section 13, steps 10-11).
# Creates what Terraform cannot create for itself: the private state bucket, the
# GitHub OIDC provider, an audit trail with its own log bucket, and a GuardDuty detector.
# Idempotent: every step checks before it writes. Needs operator credentials, never CI.
#
#   AWS_PROFILE=yucg-verse scripts/bootstrap-account.sh us-east-2
set -euo pipefail

REGION="${1:?usage: bootstrap-account.sh <region>}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STATE="yucgtfstate${ACCOUNT}"
TRAIL_BUCKET="yucgtrail${ACCOUNT}"
TAGS='TagSet=[{Key=Application,Value=club-res-website},{Key=ManagedBy,Value=bootstrap}]'
export AWS_DEFAULT_REGION="$REGION"

echo "account=$ACCOUNT region=$REGION"

private_bucket() { # $1 name
  if aws s3api head-bucket --bucket "$1" 2>/dev/null; then echo "bucket $1 exists"; return; fi
  aws s3api create-bucket --bucket "$1" --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION" \
    --object-ownership BucketOwnerEnforced >/dev/null
  aws s3api put-public-access-block --bucket "$1" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$1" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
  aws s3api put-bucket-versioning --bucket "$1" --versioning-configuration Status=Enabled
  aws s3api put-bucket-tagging --bucket "$1" --tagging "$TAGS"
  echo "bucket $1 created"
}

# --- Terraform state, saved plans, diagnostics --------------------------------
private_bucket "$STATE"
aws s3api put-bucket-policy --bucket "$STATE" --policy "$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*",
"Action":"s3:*","Resource":["arn:aws:s3:::$STATE","arn:aws:s3:::$STATE/*"],
"Condition":{"Bool":{"aws:SecureTransport":"false"}}}]}
JSON
)"
# Retention (CI_CD.md section 9 leaves this to bootstrap): plans and diagnostics are
# review artifacts, not history; state history is kept 90 days of noncurrent versions.
aws s3api put-bucket-lifecycle-configuration --bucket "$STATE" --lifecycle-configuration '{"Rules":[
{"ID":"expire-saved-plans","Status":"Enabled","Filter":{"Prefix":"ci-plans/"},
 "Expiration":{"Days":30},"NoncurrentVersionExpiration":{"NoncurrentDays":1}},
{"ID":"expire-diagnostics","Status":"Enabled","Filter":{"Prefix":"ci-diagnostics/"},
 "Expiration":{"Days":30},"NoncurrentVersionExpiration":{"NoncurrentDays":1}},
{"ID":"state-history","Status":"Enabled","Filter":{"Prefix":""},
 "NoncurrentVersionExpiration":{"NoncurrentDays":90},
 "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'

# --- GitHub OIDC provider (one per account; Terraform runs with manage=false) -
if aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text \
   | grep -q 'oidc-provider/token.actions.githubusercontent.com'; then
  echo "oidc provider exists"
else
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 1c58a3a8518e8759bf075b76b750d4f2df264fcd >/dev/null
  echo "oidc provider created"
fi

# --- Audit trail ---------------------------------------------------------------
private_bucket "$TRAIL_BUCKET"
TRAIL_ARN="arn:aws:cloudtrail:${REGION}:${ACCOUNT}:trail/yucg-audit"
aws s3api put-bucket-policy --bucket "$TRAIL_BUCKET" --policy "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*",
 "Resource":["arn:aws:s3:::$TRAIL_BUCKET","arn:aws:s3:::$TRAIL_BUCKET/*"],
 "Condition":{"Bool":{"aws:SecureTransport":"false"}}},
{"Sid":"CloudTrailAclCheck","Effect":"Allow","Principal":{"Service":"cloudtrail.amazonaws.com"},
 "Action":"s3:GetBucketAcl","Resource":"arn:aws:s3:::$TRAIL_BUCKET",
 "Condition":{"StringEquals":{"aws:SourceArn":"$TRAIL_ARN"}}},
{"Sid":"CloudTrailWrite","Effect":"Allow","Principal":{"Service":"cloudtrail.amazonaws.com"},
 "Action":"s3:PutObject","Resource":"arn:aws:s3:::$TRAIL_BUCKET/AWSLogs/$ACCOUNT/*",
 "Condition":{"StringEquals":{"s3:x-amz-acl":"bucket-owner-full-control","aws:SourceArn":"$TRAIL_ARN"}}}]}
JSON
)"
aws s3api put-bucket-lifecycle-configuration --bucket "$TRAIL_BUCKET" --lifecycle-configuration '{"Rules":[
{"ID":"audit-retention","Status":"Enabled","Filter":{"Prefix":""},
 "Expiration":{"Days":400},"NoncurrentVersionExpiration":{"NoncurrentDays":30},
 "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'
if aws cloudtrail describe-trails --trail-name-list yucg-audit --query 'trailList[0].Name' --output text 2>/dev/null | grep -q yucg-audit; then
  echo "trail exists"
else
  aws cloudtrail create-trail --name yucg-audit --s3-bucket-name "$TRAIL_BUCKET" \
    --is-multi-region-trail --include-global-service-events --enable-log-file-validation >/dev/null
  echo "trail created"
fi
aws cloudtrail start-logging --name yucg-audit

# --- GuardDuty -------------------------------------------------------------------
if [ -n "$(aws guardduty list-detectors --query 'DetectorIds[0]' --output text | grep -v None || true)" ]; then
  echo "guardduty detector exists"
else
  aws guardduty create-detector --enable --finding-publishing-frequency SIX_HOURS >/dev/null
  echo "guardduty detector created"
fi

echo "state bucket: $STATE   trail bucket: $TRAIL_BUCKET"
