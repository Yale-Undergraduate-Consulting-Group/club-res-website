mock_provider "aws" {
  mock_data "aws_organizations_organization" {
    defaults = {
      roots = [{
        id           = "r-ab12"
        arn          = "arn:aws:organizations::111111111111:root/o-abcdefghij/r-ab12"
        name         = "Root"
        policy_types = [{ status = "ENABLED", type = "SERVICE_CONTROL_POLICY" }]
      }]
    }
  }
  mock_resource "aws_organizations_organizational_unit" {
    defaults = {
      id  = "ou-ab12-11111111"
      arn = "arn:aws:organizations::111111111111:ou/o-abcdefghij/ou-ab12-11111111"
    }
  }
  mock_resource "aws_organizations_account" {
    defaults = {
      id  = "222222222222"
      arn = "arn:aws:organizations::111111111111:account/o-abcdefghij/222222222222"
    }
  }
  mock_resource "aws_organizations_policy" {
    defaults = {
      id  = "p-abcd1234"
      arn = "arn:aws:organizations::111111111111:policy/o-abcdefghij/service_control_policy/p-abcd1234"
    }
  }
}

variables {
  accounts = {
    dev  = { name = "YUCG_Dev", email = "yucg+dev@example.org" }
    prod = { name = "YUCG_Prod", email = "yucg+prod@example.org" }
  }
}

run "vends_two_accounts_under_a_guarded_ou" {
  command = apply

  assert {
    condition     = aws_organizations_organizational_unit.yucg[0].parent_id == "r-ab12"
    error_message = "The YUCG OU must sit under the organization root by default."
  }
  assert {
    condition     = length(aws_organizations_account.member) == 2 && alltrue([for a in aws_organizations_account.member : a.role_name == "OrganizationAccountAccessRole" && a.parent_id == "ou-ab12-11111111" && a.iam_user_access_to_billing == "DENY" && !a.close_on_deletion])
    error_message = "Both accounts must be created in the OU with the access role and without close-on-delete."
  }
  assert {
    condition     = aws_organizations_policy_attachment.guardrails[0].target_id == "ou-ab12-11111111"
    error_message = "The guardrail SCP must attach to the OU, not the root."
  }
  assert {
    condition     = output.github_environment_variables.prod == { AWS_ACCOUNT_ID = "222222222222", AWS_REGION = "us-east-2" }
    error_message = "Each environment gets its account id and region."
  }
}

run "guardrail_keeps_accounts_and_locks_region" {
  command = plan

  assert {
    condition = anytrue([
      for s in jsondecode(aws_organizations_policy.guardrails.content).Statement :
      s.Effect == "Deny" && contains(flatten([try(s.Action, [])]), "organizations:LeaveOrganization")
    ])
    error_message = "The guardrail must deny LeaveOrganization."
  }
  assert {
    condition = anytrue([
      for s in jsondecode(aws_organizations_policy.guardrails.content).Statement :
      s.Effect == "Deny" && can(s.NotAction) && try(s.Condition.StringNotEquals["aws:RequestedRegion"], null) == ["us-east-2"]
    ])
    error_message = "The guardrail must deny regional services outside us-east-2."
  }
}

run "reuses_an_existing_ou" {
  command = plan

  variables {
    existing_ou_id = "ou-ab12-22222222"
  }

  assert {
    condition     = length(aws_organizations_organizational_unit.yucg) == 0 && aws_organizations_policy_attachment.guardrails[0].target_id == "ou-ab12-22222222"
    error_message = "An existing OU replaces the created one, guardrail included."
  }
}

run "rejects_a_bad_email" {
  command = plan

  variables {
    accounts = {
      dev = { name = "YUCG_Dev", email = "not-an-email" }
    }
  }

  expect_failures = [var.accounts]
}
