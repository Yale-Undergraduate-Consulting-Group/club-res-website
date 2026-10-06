# Organization: vend the dev and prod accounts

Runs in an AWS Organization **management account**. Creates the `YUCG` OU (or reuses `existing_ou_id`), the member accounts `YUCG_Dev` and `YUCG_Prod` with `OrganizationAccountAccessRole`, and the `YUCG-guardrails` SCP attached to the OU. Nothing names a specific organization: in another organization the same code is a fresh plan and apply. Root-attached SCPs of the host organization still apply to the OU.

**Account creation cannot be undone by Terraform.** `close_on_deletion = false` and `prevent_destroy` keep a plan from closing an account; closing one is a manual console action by the organization owner, and the email stays bound to it.

## One-time prerequisites (a human with management-account administrator access)

1. Sign in to the **management account**, open AWS CloudShell, and paste the whole of [`cloudshell-setup.sh`](cloudshell-setup.sh). It refuses to run anywhere else. It creates the private state bucket, the GitHub OIDC provider, and the role `yucg-organization` (trust pinned to the two `organization-*` environments, permissions limited to Organizations, the state bucket, and `OrganizationAccountAccessRole`), then prints `ORG_ACCOUNT_ID`, `AWS_ORGANIZATION_ROLE_ARN`, `ORG_STATE_BUCKET` and `ORG_STATE_KEY`. Safe to run again; the script is the only definition of that role, so change it there.
2. Send the printed values to a maintainer.
3. GitHub environments `organization-plan` and `organization-apply`, deployment branch `prod` only, a maintainer as required reviewer on `-apply`, each with the same variables: `ORG_ACCOUNT_ID`, `AWS_ORGANIZATION_ROLE_ARN`, `ORG_DEV_EMAIL`, `ORG_PROD_EMAIL` (unique inboxes the club controls), `ORG_STATE_BUCKET`, `ORG_STATE_KEY` (e.g. `club-res-website/organization/terraform.tfstate`).

## Running

1. Actions → Organization → `plan` on `prod`. Review the summary (run ID, SHA256, resource actions).
2. Within 24 hours dispatch `apply` with that run ID and SHA256. The same job then runs `scripts/vend-bootstrap.sh <account> us-east-2` for every account in the state (idempotent).
3. Outputs `github_environment_variables` and `next_step_commands` (`terraform -chdir=organization output`) give each account's GitHub variables and its first provisioning command; continue with [terraform/README.md](../terraform/README.md) step 2.
