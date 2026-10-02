mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  environment        = "beta"
  monthly_budget_usd = 10
  budget_email       = "treasurer@example.org"
  tf_state_bucket    = "club-res-website-tfstate-test"
  tf_state_key       = "club-res-website/beta.tfstate"
}

run "site_bucket_is_private" {
  command = plan

  assert {
    condition     = aws_s3_bucket_public_access_block.site.block_public_acls && aws_s3_bucket_public_access_block.site.block_public_policy && aws_s3_bucket_public_access_block.site.ignore_public_acls && aws_s3_bucket_public_access_block.site.restrict_public_buckets
    error_message = "The site bucket must block every form of public access."
  }
  assert {
    condition     = one(aws_s3_bucket_ownership_controls.site.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs must be disabled (BucketOwnerEnforced) so no object can be made public."
  }
  assert {
    condition     = alltrue([for r in aws_s3_bucket_server_side_encryption_configuration.site.rule : r.apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"])
    error_message = "The site bucket must use SSE-S3 encryption."
  }
  assert {
    condition     = aws_s3_bucket_versioning.site.versioning_configuration[0].status == "Enabled"
    error_message = "Versioning keeps replaced pages recoverable."
  }
}

run "cloudfront_reads_through_origin_access_control" {
  command = plan

  assert {
    condition     = aws_cloudfront_origin_access_control.site.origin_access_control_origin_type == "s3" && aws_cloudfront_origin_access_control.site.signing_behavior == "always" && aws_cloudfront_origin_access_control.site.signing_protocol == "sigv4"
    error_message = "CloudFront must sign every request to the bucket with Origin Access Control."
  }
  assert {
    condition     = length(aws_cloudfront_distribution.site.origin) == 1 && length(one(aws_cloudfront_distribution.site.origin).s3_origin_config) == 0 && length(one(aws_cloudfront_distribution.site.origin).custom_origin_config) == 0
    error_message = "The only origin must be the private bucket via Origin Access Control: no legacy origin access identity and no public website endpoint."
  }
  assert {
    condition     = one(aws_cloudfront_distribution.site.default_cache_behavior).viewer_protocol_policy == "redirect-to-https"
    error_message = "Viewers must be redirected to HTTPS."
  }
  assert {
    condition     = aws_cloudfront_distribution.site.price_class == "PriceClass_100" && aws_cloudfront_distribution.site.default_root_object == "index.html"
    error_message = "The distribution must serve index.html from the lowest-cost price class."
  }
}

run "role_trust_is_pinned_to_the_environment" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:beta"
    error_message = "The deploy role must trust only the beta GitHub environment of this repository."
  }
  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Principal.Federated == "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    error_message = "The deploy role must trust only the GitHub OIDC provider of this account."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role.terraform.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]) == toset(["repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:infrastructure-beta-plan", "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:infrastructure-beta-apply"])
    error_message = "The Terraform role must trust only the beta infrastructure plan and apply environments."
  }
}

run "production_trust_follows_the_environment" {
  command = plan

  variables {
    environment  = "production"
    tf_state_key = "club-res-website/production.tfstate"
  }

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:Yale-Undergraduate-Consulting-Group/club-res-website:environment:production"
    error_message = "The production deploy role must trust only the production GitHub environment."
  }
  assert {
    condition     = aws_s3_bucket.site.bucket == "club-res-website-production-site-123456789012"
    error_message = "Each environment and account gets its own site bucket."
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

run "rejects_unknown_environment" {
  command = plan

  variables {
    environment = "staging"
  }

  expect_failures = [var.environment]
}
