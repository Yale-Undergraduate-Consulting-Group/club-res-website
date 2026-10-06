#!/usr/bin/env bash
# Trusted reviewed prod only. Saved plans stay in the private encrypted state
# bucket, never in public artifacts. Apply runs only the exact plan whose SHA256
# a reviewer approved, under the remote state lock.
# TF_DIR selects the root: terraform (default; TF_TARGET dev or prod) or
# organization (TF_TARGET organization, management account). TF_TARGET is the
# namespace of saved plans, diagnostics and the manifest.
set -euo pipefail
umask 0077
: "${TF_STATE_BUCKET:?}" "${TF_STATE_KEY:?}" "${TF_ACCOUNT_ID:?}" "${TF_REGION:?}"
: "${GITHUB_SHA:?}" "${TF_TARGET:?}"
: "${GITHUB_RUN_ID:?}" "${GITHUB_REPOSITORY:?}"
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ ]]
TF_DIR="${TF_DIR:-terraform}"
case "$TF_DIR:$TF_TARGET" in terraform:dev | terraform:prod | organization:organization) ;; *) exit 1 ;; esac
test "$(aws sts get-caller-identity --query Account --output text)" = "$TF_ACCOUNT_ID"
aws s3api get-public-access-block --bucket "$TF_STATE_BUCKET" --expected-bucket-owner "$TF_ACCOUNT_ID" --query PublicAccessBlockConfiguration | jq -e '.BlockPublicAcls == true and .BlockPublicPolicy == true and .IgnorePublicAcls == true and .RestrictPublicBuckets == true' >/dev/null
aws s3api get-bucket-versioning --bucket "$TF_STATE_BUCKET" --expected-bucket-owner "$TF_ACCOUNT_ID" | jq -e '.Status == "Enabled"' >/dev/null
TF_RELEASE_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TF_RELEASE_DIR"' EXIT
private_failure() {
  local phase="$1"
  local key="ci-diagnostics/$TF_TARGET/$GITHUB_SHA/$GITHUB_RUN_ID/$phase.log"
  if aws s3 cp "$TF_RELEASE_DIR/$phase.log" "s3://$TF_STATE_BUCKET/$key" --sse AES256 --only-show-errors; then
    echo "Terraform $phase failed. Authorized operators can inspect the encrypted log at $key." >&2
    echo "Terraform $phase failed. Private diagnostic key: $key" >> "$GITHUB_STEP_SUMMARY"
  else
    echo "Terraform $phase failed; private diagnostic upload also failed. No sensitive log was printed." >&2
  fi
  exit 1
}
export TF_IN_AUTOMATION=true TF_INPUT=false
terraform -chdir="$TF_DIR" init -lockfile=readonly \
  -backend-config="bucket=$TF_STATE_BUCKET" -backend-config="key=$TF_STATE_KEY" \
  -backend-config="region=$TF_REGION" -backend-config=encrypt=true > "$TF_RELEASE_DIR/init.log" 2>&1 || private_failure init
if [ "${1:-}" = plan ]; then
  # Input variables belong to plan only; apply executes the saved plan as reviewed.
  export TF_VAR_aws_region="$TF_REGION"
  if [ "$TF_DIR" = organization ]; then
    : "${ORG_DEV_EMAIL:?}" "${ORG_PROD_EMAIL:?}"
    TF_VAR_accounts="$(jq -cn --arg dev "$ORG_DEV_EMAIL" --arg prod "$ORG_PROD_EMAIL" \
      '{dev:{name:"YUCG_Dev",email:$dev},prod:{name:"YUCG_Prod",email:$prod}}')"
    export TF_VAR_accounts
  else
    : "${TF_BUDGET_EMAIL:?}" "${TF_MONTHLY_BUDGET_USD:?}"
    export TF_VAR_environment="$TF_TARGET" TF_VAR_github_repository="$GITHUB_REPOSITORY"
    export TF_VAR_monthly_budget_usd="$TF_MONTHLY_BUDGET_USD" TF_VAR_budget_email="$TF_BUDGET_EMAIL"
    export TF_VAR_tf_state_bucket="$TF_STATE_BUCKET" TF_VAR_tf_state_key="$TF_STATE_KEY"
    export TF_VAR_manage_github_oidc_provider="${TF_MANAGE_GITHUB_OIDC_PROVIDER:-true}"
    # Optional per-environment settings, e.g. TF_INSTANCE_TYPE=t3.medium or
    # TF_CATALOG_OPERATOR_PRINCIPAL_ARNS='["arn:aws:iam::<account>:role/<name>"]'. Unset keeps
    # the Terraform default; every value becomes part of the reviewed saved plan.
    for name in instance_type data_volume_gb enable_edge enable_waf origin_read_timeout office_hours_enabled catalog_operator_principal_arns; do
      setting="TF_${name^^}"
      if [ -n "${!setting:-}" ]; then export "TF_VAR_${name}=${!setting}"; fi
    done
  fi
  PREFIX="ci-plans/$TF_TARGET/$GITHUB_SHA/$GITHUB_RUN_ID"
  terraform -chdir="$TF_DIR" plan -lock-timeout=60s -out="$TF_RELEASE_DIR/saved.tfplan" > "$TF_RELEASE_DIR/plan.log" 2>&1 || private_failure plan
  terraform -chdir="$TF_DIR" show -json "$TF_RELEASE_DIR/saved.tfplan" > "$TF_RELEASE_DIR/plan.json"
  PLAN_SHA="$(sha256sum "$TF_RELEASE_DIR/saved.tfplan" | cut -d ' ' -f 1)"
  jq -n --arg sha "$PLAN_SHA" --arg commit "$GITHUB_SHA" --arg account "$TF_ACCOUNT_ID" \
    --arg key "$TF_STATE_KEY" --arg bucket "$TF_STATE_BUCKET" --arg region "$TF_REGION" --arg target "$TF_TARGET" --arg repo "$GITHUB_REPOSITORY" \
    '{sha256:$sha,commit:$commit,account:$account,state_key:$key,state_bucket:$bucket,region:$region,target:$target,repository:$repo,created_at:now}' > "$TF_RELEASE_DIR/manifest.json"
  for file in saved.tfplan plan.json manifest.json; do
    aws s3 cp "$TF_RELEASE_DIR/$file" "s3://$TF_STATE_BUCKET/$PREFIX/$file" --sse AES256 --only-show-errors
  done
  # Plan JSON can contain secrets: only resource addresses and actions are printed.
  {
    printf 'Saved plan run: %s\nCommit: %s\nSHA256: %s\nTarget: %s\n\n' "$GITHUB_RUN_ID" "$GITHUB_SHA" "$PLAN_SHA" "$TF_TARGET"
    echo '| Resource | Actions |'
    echo '| --- | --- |'
    jq -r '.resource_changes[]? | select(.change.actions != ["no-op"]) | "| \(.address) | \(.change.actions | join(", ")) |"' "$TF_RELEASE_DIR/plan.json"
    printf '\nApply only after reviewing this plan: dispatch operation apply with this run ID and SHA256 within 24 hours. Cost delta requires review of the private plan.\n'
  } >> "$GITHUB_STEP_SUMMARY"
elif [ "${1:-}" = apply ]; then
  [[ "${PLAN_RUN_ID:-}" =~ ^[0-9]+$ ]]
  [[ "${APPROVED_PLAN_SHA256:-}" =~ ^[a-f0-9]{64}$ ]]
  PREFIX="ci-plans/$TF_TARGET/$GITHUB_SHA/$PLAN_RUN_ID"
  for file in saved.tfplan manifest.json; do
    aws s3 cp "s3://$TF_STATE_BUCKET/$PREFIX/$file" "$TF_RELEASE_DIR/$file" --only-show-errors
  done
  jq -e --arg sha "$APPROVED_PLAN_SHA256" --arg commit "$GITHUB_SHA" --arg account "$TF_ACCOUNT_ID" \
    --arg key "$TF_STATE_KEY" --arg bucket "$TF_STATE_BUCKET" --arg region "$TF_REGION" --arg target "$TF_TARGET" --arg repo "$GITHUB_REPOSITORY" \
    '.sha256 == $sha and .commit == $commit and .account == $account and .state_key == $key and .state_bucket == $bucket and .region == $region and .target == $target and .repository == $repo and (now - .created_at >= 0 and now - .created_at < 86400)' "$TF_RELEASE_DIR/manifest.json" >/dev/null
  echo "$APPROVED_PLAN_SHA256  $TF_RELEASE_DIR/saved.tfplan" | sha256sum -c -
  terraform -chdir="$TF_DIR" apply -lock-timeout=60s "$TF_RELEASE_DIR/saved.tfplan" > "$TF_RELEASE_DIR/apply.log" 2>&1 || private_failure apply
  echo 'Applied the exact approved plan under the remote state lock.' >> "$GITHUB_STEP_SUMMARY"
else
  echo 'Use plan or apply' >&2; exit 1
fi
