mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  mock_data "aws_ssm_parameter" {
    defaults = {
      value = "ami-0123456789abcdef0"
    }
  }
  mock_data "aws_security_group" {
    defaults = {
      id = "sg-0cf0000000000000a"
    }
  }
  # The provider validates ARN-shaped arguments even for mocked resources, so
  # resources whose ARNs feed another resource need a well-formed value.
  mock_resource "aws_kms_key" {
    defaults = {
      arn    = "arn:aws:kms:us-east-2:123456789012:key/00000000-0000-0000-0000-000000000000"
      key_id = "00000000-0000-0000-0000-000000000000"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-2:123456789012:log-group:mock"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::mock"
    }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-2:123456789012:secret:mock-AbCdEf"
    }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn = "arn:aws:ecr:us-east-2:123456789012:repository/mock"
    }
  }
  mock_resource "aws_instance" {
    defaults = {
      arn = "arn:aws:ec2:us-east-2:123456789012:instance/i-0123456789abcdef0"
    }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = {
      arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    }
  }
  mock_resource "aws_cloudfront_function" {
    defaults = {
      arn = "arn:aws:cloudfront::123456789012:function/mock"
    }
  }
}

mock_provider "aws" {
  alias = "us_east_1"
  mock_resource "aws_wafv2_web_acl" {
    defaults = {
      arn = "arn:aws:wafv2:us-east-1:123456789012:global/webacl/mock/00000000-0000-0000-0000-000000000000"
    }
  }
}

variables {
  environment        = "dev"
  monthly_budget_usd = 10
  budget_email       = "treasurer@example.org"
  tf_state_bucket    = "club-res-website-tfstate-test"
  tf_state_key       = "club-res-website/dev.tfstate"
}

run "dev_has_no_public_door" {
  command = apply

  assert {
    condition     = length(aws_cloudfront_distribution.site) == 0 && length(aws_s3_bucket.site) == 0 && length(aws_wafv2_web_acl.edge) == 0
    error_message = "Dev has no CloudFront, site bucket or WAF; developers use SSM port forwarding."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.cloudfront) == 0
    error_message = "The dev box must accept no inbound traffic."
  }
  assert {
    condition     = jsondecode(aws_ssm_parameter.config.value).public_url == "http://localhost:8000" && jsondecode(aws_ssm_parameter.config.value).edge == false && jsondecode(aws_ssm_parameter.config.value).email_delivery_enabled == false
    error_message = "Dev config must point at the port-forwarded container, bind locally and never deliver email."
  }
  assert {
    condition     = output.site_bucket == "" && output.distribution_id == "" && output.site_url == ""
    error_message = "Dev leaves the SITE_* GitHub variables empty."
  }
  assert {
    condition     = length([for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : s if startswith(s.Sid, "PublishSite") || startswith(s.Sid, "InvalidateSite")]) == 0
    error_message = "The dev deploy role has nothing to publish to S3 or CloudFront."
  }
}

run "box_is_hardened" {
  command = apply

  assert {
    condition     = aws_instance.box.metadata_options[0].http_tokens == "required" && aws_instance.box.metadata_options[0].http_put_response_hop_limit == 2
    error_message = "The box requires IMDSv2, with one extra hop for the container."
  }
  assert {
    condition     = aws_instance.box.root_block_device[0].encrypted && aws_ebs_volume.data.encrypted && aws_ebs_volume.data.kms_key_id == aws_kms_key.env.arn && aws_kms_key.env.enable_key_rotation
    error_message = "Both volumes must be encrypted; the data volume with the rotating environment key."
  }
  assert {
    condition     = alltrue([for r in aws_vpc_security_group_egress_rule.box : contains([80, 443], r.from_port) if r.cidr_ipv4 == "0.0.0.0/0"]) && alltrue([for r in aws_vpc_security_group_egress_rule.box : r.cidr_ipv4 == "10.20.0.2/32" if r.from_port == 53])
    error_message = "Internet egress is HTTP(S) only, and DNS goes only to the VPC resolver behind DNS Firewall."
  }
  assert {
    condition     = toset([for r in aws_route53_resolver_firewall_rule.managed : r.action]) == toset(["BLOCK"]) && aws_route53_resolver_firewall_rule_group_association.main.vpc_id == aws_vpc.main.id
    error_message = "DNS Firewall must block the managed threat lists in this VPC."
  }
  assert {
    condition     = aws_flow_log.vpc.vpc_id == aws_vpc.main.id && aws_route53_resolver_query_log_config_association.main.resource_id == aws_vpc.main.id
    error_message = "VPC flow logs and DNS query logs must cover the VPC."
  }
}

run "data_buckets_are_private_encrypted_and_vpc_bound" {
  command = apply

  assert {
    condition     = alltrue([for b in aws_s3_bucket_public_access_block.data : b.block_public_acls && b.block_public_policy && b.ignore_public_acls && b.restrict_public_buckets])
    error_message = "Client data buckets must block every form of public access."
  }
  assert {
    condition     = alltrue([for c in aws_s3_bucket_server_side_encryption_configuration.data : alltrue([for r in c.rule : r.apply_server_side_encryption_by_default[0].sse_algorithm == "aws:kms" && r.apply_server_side_encryption_by_default[0].kms_master_key_id == aws_kms_key.env.arn])])
    error_message = "Client data buckets must use the environment key."
  }
  assert {
    condition     = alltrue([for v in aws_s3_bucket_versioning.data : v.versioning_configuration[0].status == "Enabled"])
    error_message = "Versioning keeps replaced client data recoverable."
  }
  assert {
    condition     = alltrue([for p in aws_s3_bucket_policy.data : one([for s in jsondecode(p.policy).Statement : s.Condition.StringNotEquals["aws:sourceVpce"] if s.Sid == "ObjectsOnlyThroughThisVpc"]) == aws_vpc_endpoint.s3.id])
    error_message = "Client data objects must be reachable only through this VPC's S3 endpoint."
  }
  assert {
    condition     = aws_secretsmanager_secret.app.kms_key_id == aws_kms_key.env.arn
    error_message = "The app secret must use the environment key."
  }
  assert {
    condition     = toset(one([for s in jsondecode(aws_s3_bucket_policy.documents.policy).Statement : s.Condition.ArnNotLike["aws:PrincipalArn"] if s.Sid == "ObjectsOnlyForTheBoxRole"])) == toset([aws_iam_role.box.arn])
    error_message = "Only the box role, whose presigned URLs browsers use, may touch document objects."
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.documents.cors_rule).allowed_origins == toset(["http://localhost:8000"])
    error_message = "In dev, document CORS admits only the SSM port-forward origin."
  }
  assert {
    condition     = jsondecode(aws_ssm_parameter.config.value).documents_bucket == aws_s3_bucket.data["documents"].bucket
    error_message = "The box config must name the documents bucket."
  }
}

run "deploy_role_runs_commands_only_on_its_box" {
  command = apply

  assert {
    condition     = one([for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : s.Condition.StringEquals["ssm:resourceTag/Site"] if s.Sid == "RunOnlyOnThisEnvironmentsBox"]) == "club-res-website-dev"
    error_message = "SendCommand must be limited to instances tagged with this environment's Site."
  }
}

run "role_trust_is_pinned_to_the_environment" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:dev"
    error_message = "The deploy role must trust only the dev GitHub environment of this repository."
  }
  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Principal.Federated == "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    error_message = "The deploy role must trust only the GitHub OIDC provider of this account."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role.terraform.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]) == toset(["repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:infrastructure-dev-plan", "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:infrastructure-dev-apply"])
    error_message = "The Terraform role must trust only the dev infrastructure plan and apply environments."
  }
}

run "shared_account_reuses_the_existing_oidc_provider" {
  command = plan

  variables {
    manage_github_oidc_provider = false
  }

  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0
    error_message = "The provider must not be created when the account already has it."
  }
  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Principal.Federated == "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    error_message = "The roles must trust the account's existing GitHub OIDC provider."
  }
}

run "office_hours_schedule_is_opt_in" {
  command = plan

  variables {
    office_hours_enabled = true
  }

  assert {
    condition     = toset(keys(aws_scheduler_schedule.office_hours)) == toset(["start", "stop"]) && aws_scheduler_schedule.office_hours["stop"].target[0].arn == "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    error_message = "office_hours_enabled must add one start and one stop schedule for the box."
  }
}

run "rejects_unknown_environment" {
  command = plan

  variables {
    environment = "staging"
  }

  expect_failures = [var.environment]
}

run "rejects_regions_outside_the_guardrail" {
  command = plan

  variables {
    aws_region = "us-east-1"
  }

  expect_failures = [var.aws_region]
}

run "rejects_origin_timeouts_above_the_cloudfront_maximum" {
  command = plan

  variables {
    origin_read_timeout = 180
  }

  expect_failures = [var.origin_read_timeout]
}

run "prod_edge_routes_api_through_the_vpc_origin" {
  command = apply

  variables {
    environment  = "prod"
    tf_state_key = "club-res-website/prod.tfstate"
  }

  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.cloudfront) == 1 && aws_vpc_security_group_ingress_rule.cloudfront[0].referenced_security_group_id == data.aws_security_group.cloudfront_vpc_origin[0].id && aws_vpc_security_group_ingress_rule.cloudfront[0].cidr_ipv4 == null && aws_vpc_security_group_ingress_rule.cloudfront[0].from_port == 80 && aws_vpc_security_group_ingress_rule.cloudfront[0].to_port == 80
    error_message = "The prod box must accept only tcp/80 from the CloudFront VPC origin security group."
  }
  assert {
    condition     = one([for o in aws_cloudfront_distribution.site[0].origin : o.vpc_origin_config[0].vpc_origin_id if length(o.vpc_origin_config) > 0]) == aws_cloudfront_vpc_origin.api[0].id
    error_message = "/api must reach the box only through the CloudFront VPC origin."
  }
  assert {
    condition     = toset([for b in aws_cloudfront_distribution.site[0].ordered_cache_behavior : b.path_pattern if b.target_origin_id == "box-api" && b.cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"]) == toset(["/api", "/api/*"])
    error_message = "Every /api request must go to the box uncached."
  }
  assert {
    condition     = one([for o in aws_cloudfront_distribution.site[0].origin : o.origin_access_control_id if length(o.vpc_origin_config) == 0]) == aws_cloudfront_origin_access_control.site[0].id && aws_cloudfront_origin_access_control.site[0].signing_behavior == "always"
    error_message = "CloudFront must read the private site bucket through Origin Access Control."
  }
  assert {
    condition     = aws_cloudfront_distribution.site[0].default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https" && alltrue([for b in aws_cloudfront_distribution.site[0].ordered_cache_behavior : b.viewer_protocol_policy == "redirect-to-https"])
    error_message = "Viewers must be redirected to HTTPS."
  }
  assert {
    condition     = aws_cloudfront_distribution.site[0].web_acl_id == aws_wafv2_web_acl.edge[0].arn && aws_wafv2_web_acl.edge[0].scope == "CLOUDFRONT"
    error_message = "The distribution must sit behind the CloudFront-scope WAF by default."
  }
  assert {
    condition     = jsondecode(aws_ssm_parameter.config.value).public_url == "https://${aws_cloudfront_distribution.site[0].domain_name}" && jsondecode(aws_ssm_parameter.config.value).edge && jsondecode(aws_ssm_parameter.config.value).email_delivery_enabled
    error_message = "Prod config must name the CloudFront URL, listen for the VPC origin and deliver email."
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.documents.cors_rule).allowed_origins == toset(["https://${aws_cloudfront_distribution.site[0].domain_name}"])
    error_message = "In prod, document CORS admits only the CloudFront origin."
  }
  assert {
    condition     = aws_s3_bucket.site[0].bucket == "club-res-website-prod-site-123456789012"
    error_message = "Each environment and account gets its own site bucket."
  }
}

run "waf_can_be_turned_off" {
  command = plan

  variables {
    environment  = "prod"
    tf_state_key = "club-res-website/prod.tfstate"
    enable_waf   = false
  }

  assert {
    condition     = length(aws_wafv2_web_acl.edge) == 0
    error_message = "enable_waf = false must remove the web ACL."
  }
}

run "production_trust_follows_the_environment" {
  command = plan

  variables {
    environment  = "prod"
    tf_state_key = "club-res-website/prod.tfstate"
  }

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:prod"
    error_message = "The production deploy role must trust only the prod GitHub environment."
  }
}
