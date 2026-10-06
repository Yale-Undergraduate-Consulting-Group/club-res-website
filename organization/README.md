# Organization: vend the dev and prod accounts

Runs in an AWS Organization **management account**. Creates the `YUCG` OU (or reuses `existing_ou_id`), the member accounts `YUCG_Dev` and `YUCG_Prod` with `OrganizationAccountAccessRole`, and the `YUCG-guardrails` SCP attached to the OU. Nothing names a specific organization: in another organization the same code is a fresh plan and apply. Root-attached SCPs of the host organization still apply to the OU.

**Account creation cannot be undone by Terraform.** `close_on_deletion = false` and `prevent_destroy` keep a plan from closing an account; closing one is a manual console action by the organization owner, and the email stays bound to it.

## One-time prerequisites (a human with management-account administrator access)

1. State bucket and GitHub OIDC provider: run `scripts/bootstrap-account.sh us-east-2` with management-account operator credentials (it also adds an audit trail and GuardDuty), or create an equivalent private, versioned, encrypted bucket and the `token.actions.githubusercontent.com` provider by hand.
2. IAM role for `AWS_ORGANIZATION_ROLE_ARN`, max session 1 hour. Trust (replace `<ORG_ACCOUNT_ID>`):

```json
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"sts:AssumeRoleWithWebIdentity",
 "Principal":{"Federated":"arn:aws:iam::<ORG_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"},
 "Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com","token.actions.githubusercontent.com:sub":[
  "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:organization-plan",
  "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:organization-apply"]}}}]}
```

   Inline policy (replace `<BUCKET>` and `<KEY>` with `ORG_STATE_BUCKET` and `ORG_STATE_KEY`):

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadOrganization","Effect":"Allow","Action":["organizations:Describe*","organizations:List*"],"Resource":"*"},
 {"Sid":"VendAccounts","Effect":"Allow","Resource":"*","Action":["organizations:CreateAccount","organizations:MoveAccount",
  "organizations:CreateOrganizationalUnit","organizations:UpdateOrganizationalUnit","organizations:CreatePolicy","organizations:UpdatePolicy",
  "organizations:AttachPolicy","organizations:DetachPolicy","organizations:TagResource","organizations:UntagResource"]},
 {"Sid":"BootstrapVendedAccounts","Effect":"Allow","Action":"sts:AssumeRole","Resource":"arn:aws:iam::*:role/OrganizationAccountAccessRole"},
 {"Sid":"CheckStateBucket","Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketVersioning","s3:GetBucketPublicAccessBlock"],"Resource":"arn:aws:s3:::<BUCKET>"},
 {"Sid":"StateAndSavedPlans","Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":["arn:aws:s3:::<BUCKET>/<KEY>",
  "arn:aws:s3:::<BUCKET>/ci-plans/organization/*","arn:aws:s3:::<BUCKET>/ci-diagnostics/organization/*"]},
 {"Sid":"StateLock","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::<BUCKET>/<KEY>.tflock"}]}
```

3. GitHub environments `organization-plan` and `organization-apply`, deployment branch `prod` only, a maintainer as required reviewer on `-apply`, each with the same variables: `ORG_ACCOUNT_ID`, `AWS_ORGANIZATION_ROLE_ARN`, `ORG_DEV_EMAIL`, `ORG_PROD_EMAIL` (unique inboxes the club controls), `ORG_STATE_BUCKET`, `ORG_STATE_KEY` (e.g. `club-res-website/organization/terraform.tfstate`).

## Running

1. Actions → Organization → `plan` on `prod`. Review the summary (run ID, SHA256, resource actions).
2. Within 24 hours dispatch `apply` with that run ID and SHA256. The same job then runs `scripts/vend-bootstrap.sh <account> us-east-2` for every account in the state (idempotent).
3. Outputs `github_environment_variables` and `next_step_commands` (`terraform -chdir=organization output`) give each account's GitHub variables and its first provisioning command; continue with [terraform/README.md](../terraform/README.md) step 2.
