terraform {
  required_version = ">= 1.14, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  # scripts/terraform-release.sh supplies bucket, key and region with
  # -backend-config at init (TF_DIR=organization). State never enters Git.
  backend "s3" { use_lockfile = true }
}

# Runs with management-account credentials. Organizations is a global service:
# the provider always signs its calls for the us-east-1 endpoint, whatever region
# is set here, so no us-east-1 alias is needed. var.aws_region is the workload
# region handed to the vended accounts.
provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Application = "club-res-website"
      ManagedBy   = "terraform"
    }
  }
}

data "aws_organizations_organization" "this" {}

locals {
  ou_id = coalesce(var.existing_ou_id, one(aws_organizations_organizational_unit.yucg[*].id))
}

resource "aws_organizations_organizational_unit" "yucg" {
  count     = var.existing_ou_id == null ? 1 : 0
  name      = "YUCG"
  parent_id = coalesce(var.parent_id, data.aws_organizations_organization.this.roots[0].id)
}

# Creating an account cannot be undone by Terraform: close_on_deletion = false and
# prevent_destroy keep a removed map entry from closing a live account. Closing
# one is a deliberate console action by the organization owner.
resource "aws_organizations_account" "member" {
  for_each                   = var.accounts
  name                       = each.value.name
  email                      = each.value.email
  parent_id                  = local.ou_id
  role_name                  = "OrganizationAccountAccessRole"
  iam_user_access_to_billing = "DENY"
  close_on_deletion          = false

  lifecycle {
    prevent_destroy = true
    # AWS reads neither back after creation; changing them would force a new account.
    ignore_changes = [role_name, iam_user_access_to_billing]
  }
}

# The guardrail from the YUCG permission request (Step 2, Option B), unchanged.
resource "aws_organizations_policy" "guardrails" {
  name        = "YUCG-guardrails"
  description = "Keep YUCG accounts in the organization, protect the audit trail, lock regional services to us-east-2."
  type        = "SERVICE_CONTROL_POLICY"
  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "KeepAccountInOrganization"
        Effect   = "Deny"
        Action   = ["organizations:LeaveOrganization"]
        Resource = "*"
      },
      {
        Sid      = "ProtectAuditLog"
        Effect   = "Deny"
        Action   = ["cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:UpdateTrail"]
        Resource = "*"
      },
      {
        Sid    = "RegionLockExceptGlobalServices"
        Effect = "Deny"
        NotAction = [
          "acm:*", "account:*", "bedrock:*", "billing:*", "budgets:*", "ce:*",
          "cloudfront:*", "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics",
          "cloudwatch:ListMetrics", "cur:*", "freetier:*", "health:*", "iam:*", "kms:*",
          "organizations:*", "payments:*", "pricing:*", "route53:*", "route53domains:*",
          "s3:GetAccountPublic*", "s3:ListAllMyBuckets", "s3:PutAccountPublic*",
          "shield:*", "sts:*", "support:*", "tax:*", "trustedadvisor:*", "waf:*", "wafv2:*",
          "ec2:DescribeRegions"
        ]
        Resource  = "*"
        Condition = { StringNotEquals = { "aws:RequestedRegion" = ["us-east-2"] } }
      },
      {
        Sid       = "AiModelsUsRegionsOnly"
        Effect    = "Deny"
        Action    = "bedrock:*"
        Resource  = "*"
        Condition = { StringNotEquals = { "aws:RequestedRegion" = ["us-east-1", "us-east-2", "us-west-2"] } }
      }
    ]
  })
}

resource "aws_organizations_policy_attachment" "guardrails" {
  count     = var.attach_guardrails ? 1 : 0
  policy_id = aws_organizations_policy.guardrails.id
  target_id = local.ou_id
}
