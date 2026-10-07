#!/usr/bin/env bash
# One-time setup for the Organization workflow. Paste this whole file into AWS
# CloudShell while signed in to the Organizations MANAGEMENT account as an
# administrator. It creates only what the workflow cannot create for itself:
# a private state bucket, the GitHub OIDC provider, and the role the workflow
# assumes. It then prints the GitHub variables to set. Safe to run again.
set -euo pipefail

REGION=us-east-2
REPO=Yale-Undergraduate-Consulting-Group/club-res-website
# GitHub issues the immutable subject (owner and repository IDs) for repositories created after
# 2026-07-15; a trust written as owner/name never matches. gh api repos/$REPO --jq '.owner.id, .id'
SUBJECT=Yale-Undergraduate-Consulting-Group@264275789/club-res-website@1401667698
ROLE=yucg-organization
KEY=club-res-website/organization/terraform.tfstate
export AWS_DEFAULT_REGION="$REGION"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
MANAGEMENT="$(aws organizations describe-organization --query Organization.MasterAccountId --output text)"
[ "$ACCOUNT" = "$MANAGEMENT" ] || { echo "Run this in the management account ($MANAGEMENT), not $ACCOUNT." >&2; exit 1; }
BUCKET="yucgorgstate$ACCOUNT"
PROVIDER="arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com"

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "bucket $BUCKET exists"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION" --object-ownership BucketOwnerEnforced >/dev/null
  echo "bucket $BUCKET created"
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"DenyInsecureTransport\",\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":\"s3:*\",\"Resource\":[\"arn:aws:s3:::$BUCKET\",\"arn:aws:s3:::$BUCKET/*\"],\"Condition\":{\"Bool\":{\"aws:SecureTransport\":\"false\"}}}]}"
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{"Rules":[
{"ID":"expire-saved-plans","Status":"Enabled","Filter":{"Prefix":"ci-plans/"},"Expiration":{"Days":30},"NoncurrentVersionExpiration":{"NoncurrentDays":1}},
{"ID":"expire-diagnostics","Status":"Enabled","Filter":{"Prefix":"ci-diagnostics/"},"Expiration":{"Days":30},"NoncurrentVersionExpiration":{"NoncurrentDays":1}},
{"ID":"state-history","Status":"Enabled","Filter":{"Prefix":""},"NoncurrentVersionExpiration":{"NoncurrentDays":90},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER" >/dev/null 2>&1; then
  echo "oidc provider exists"
else
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 1c58a3a8518e8759bf075b76b750d4f2df264fcd >/dev/null
  echo "oidc provider created"
fi

TRUST="$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"sts:AssumeRoleWithWebIdentity",
"Principal":{"Federated":"$PROVIDER"},
"Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com","token.actions.githubusercontent.com:sub":[
"repo:$SUBJECT:environment:organization-plan","repo:$SUBJECT:environment:organization-apply"]}}}]}
JSON
)"
POLICY="$(cat <<JSON
{"Version":"2012-10-17","Statement":[
{"Sid":"ReadOrganization","Effect":"Allow","Action":["organizations:Describe*","organizations:List*"],"Resource":"*"},
{"Sid":"VendAccounts","Effect":"Allow","Resource":"*","Action":["organizations:CreateAccount","organizations:MoveAccount",
"organizations:CreateOrganizationalUnit","organizations:UpdateOrganizationalUnit","organizations:CreatePolicy","organizations:UpdatePolicy",
"organizations:AttachPolicy","organizations:DetachPolicy","organizations:TagResource","organizations:UntagResource"]},
{"Sid":"OrganizationsServiceLinkedRole","Effect":"Allow","Action":"iam:CreateServiceLinkedRole","Resource":"*",
"Condition":{"StringEquals":{"iam:AWSServiceName":"organizations.amazonaws.com"}}},
{"Sid":"BootstrapVendedAccounts","Effect":"Allow","Action":"sts:AssumeRole","Resource":"arn:aws:iam::*:role/OrganizationAccountAccessRole"},
{"Sid":"CheckStateBucket","Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketVersioning","s3:GetBucketPublicAccessBlock"],"Resource":"arn:aws:s3:::$BUCKET"},
{"Sid":"StateAndSavedPlans","Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":["arn:aws:s3:::$BUCKET/$KEY",
"arn:aws:s3:::$BUCKET/ci-plans/organization/*","arn:aws:s3:::$BUCKET/ci-diagnostics/organization/*"]},
{"Sid":"StateLock","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::$BUCKET/$KEY.tflock"}]}
JSON
)"
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST"
  echo "role $ROLE updated"
else
  aws iam create-role --role-name "$ROLE" --max-session-duration 3600 \
    --assume-role-policy-document "$TRUST" --description "GitHub Organization workflow for $REPO" >/dev/null
  echo "role $ROLE created"
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name vend-accounts --policy-document "$POLICY"

cat <<EOF

Done. Send these values to the person running the repository:

  ORG_ACCOUNT_ID            = $ACCOUNT
  AWS_ORGANIZATION_ROLE_ARN = arn:aws:iam::$ACCOUNT:role/$ROLE
  ORG_STATE_BUCKET          = $BUCKET
  ORG_STATE_KEY             = $KEY
EOF
