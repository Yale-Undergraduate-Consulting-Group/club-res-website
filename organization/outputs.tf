output "account_ids" {
  value       = { for k, a in aws_organizations_account.member : k => a.id }
  description = "Member account id per environment key; .github/workflows/organization.yml bootstraps each one."
}

output "ou_id" {
  value       = local.ou_id
  description = "OU that holds the member accounts and carries the guardrail SCP."
}

output "github_environment_variables" {
  value = {
    for k, a in aws_organizations_account.member : k => {
      AWS_ACCOUNT_ID = a.id
      AWS_REGION     = var.aws_region
    }
  }
  description = "Paste into the GitHub environment of the same name; TF_ACCOUNT_ID and TF_REGION on infrastructure-<env>-plan/-apply take the same values."
}

output "next_step_commands" {
  value = {
    for k, a in aws_organizations_account.member : k => join(" ", [
      "With credentials for arn:aws:iam::${a.id}:role/OrganizationAccountAccessRole:",
      "terraform -chdir=terraform init -backend-config=bucket=yucgtfstate${a.id}",
      "-backend-config=key=club-res-website/${k}/terraform.tfstate -backend-config=region=${var.aws_region} -backend-config=encrypt=true",
      "&& terraform -chdir=terraform apply -var environment=${k} -var manage_github_oidc_provider=false",
      "-var tf_state_bucket=yucgtfstate${a.id} -var tf_state_key=club-res-website/${k}/terraform.tfstate",
      "-var budget_email=<address> -var monthly_budget_usd=<amount>",
    ])
  }
  description = "Step 4 of terraform/README.md for each account; steps 2-3 come first and 5-8 follow."
}
