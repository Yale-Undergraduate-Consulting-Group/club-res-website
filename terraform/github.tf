# GitHub Actions OIDC: no long-lived AWS keys exist anywhere. Each role trusts
# exactly one GitHub environment of this repository.
locals {
  github_oidc_provider_arn = "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
  deploy_subject           = "repo:${var.github_repository}:environment:${var.environment}"
  terraform_subjects = [
    "repo:${var.github_repository}:environment:infrastructure-${var.environment}-plan",
    "repo:${var.github_repository}:environment:infrastructure-${var.environment}-apply",
  ]
  state_bucket_arn = "arn:aws:s3:::${var.tf_state_bucket}"
  budget_name      = "${local.name}-monthly"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.manage_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates GitHub tokens against its trusted CA library; the IAM API
  # still accepts these published GitHub thumbprints.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

# Publishes the static export: sync to the site bucket and invalidate its
# distribution. Nothing else.
resource "aws_iam_role" "deploy" {
  name                 = "${local.name}-deploy"
  description          = "GitHub environment ${var.environment} publishes the static site."
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "GitHubEnvironmentOnly"
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_provider_arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.deploy_subject
        }
      }
    }]
  })
  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy" "deploy" {
  name = "publish-static-site"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListSiteBucket"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.site.arn
      },
      {
        Sid      = "PublishSiteObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.site.arn}/*"
      },
      {
        Sid      = "InvalidateSiteCache"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
        Resource = aws_cloudfront_distribution.site.arn
      },
    ]
  })
}

# Runs reviewed plan/apply from main. It reads and updates the resources of
# this state; it cannot create or delete them. The first apply (bootstrap) and
# any change that creates, replaces or deletes a resource run with operator
# credentials, and the apply environment requires a reviewer.
resource "aws_iam_role" "terraform" {
  name                 = "${local.name}-terraform"
  description          = "GitHub environments infrastructure-${var.environment}-plan/apply run reviewed Terraform."
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "GitHubInfrastructureEnvironmentsOnly"
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_provider_arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.terraform_subjects
        }
      }
    }]
  })
  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy" "terraform" {
  name = "manage-site-stack"
  role = aws_iam_role.terraform.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "CheckStateBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketVersioning", "s3:GetBucketPublicAccessBlock"]
        Resource = local.state_bucket_arn
      },
      {
        Sid    = "ReadWriteStateAndSavedPlans"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${local.state_bucket_arn}/${var.tf_state_key}",
          "${local.state_bucket_arn}/ci-plans/${var.environment}/*",
          "${local.state_bucket_arn}/ci-diagnostics/${var.environment}/*",
        ]
      },
      {
        Sid      = "StateLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${local.state_bucket_arn}/${var.tf_state_key}.tflock"
      },
      {
        Sid    = "ManageSiteBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketAcl",
          "s3:GetBucketCORS",
          "s3:GetBucketWebsite",
          "s3:GetBucketVersioning",
          "s3:PutBucketVersioning",
          "s3:GetAccelerateConfiguration",
          "s3:GetBucketRequestPayment",
          "s3:GetBucketLogging",
          "s3:GetLifecycleConfiguration",
          "s3:PutLifecycleConfiguration",
          "s3:GetReplicationConfiguration",
          "s3:GetEncryptionConfiguration",
          "s3:PutEncryptionConfiguration",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketTagging",
          "s3:PutBucketTagging",
          "s3:GetBucketPolicy",
          "s3:PutBucketPolicy",
          "s3:GetBucketOwnershipControls",
          "s3:PutBucketOwnershipControls",
          "s3:GetBucketPublicAccessBlock",
          "s3:PutBucketPublicAccessBlock",
        ]
        Resource = aws_s3_bucket.site.arn
      },
      {
        Sid    = "ManageDistribution"
        Effect = "Allow"
        Action = [
          "cloudfront:GetDistribution",
          "cloudfront:GetDistributionConfig",
          "cloudfront:UpdateDistribution",
          "cloudfront:ListTagsForResource",
          "cloudfront:TagResource",
          "cloudfront:UntagResource",
        ]
        Resource = aws_cloudfront_distribution.site.arn
      },
      {
        Sid      = "ManageOriginAccessControl"
        Effect   = "Allow"
        Action   = ["cloudfront:GetOriginAccessControl", "cloudfront:UpdateOriginAccessControl"]
        Resource = "arn:aws:cloudfront::${local.account_id}:origin-access-control/${aws_cloudfront_origin_access_control.site.id}"
      },
      {
        Sid      = "ManageRoutesFunction"
        Effect   = "Allow"
        Action   = ["cloudfront:DescribeFunction", "cloudfront:GetFunction", "cloudfront:UpdateFunction", "cloudfront:PublishFunction"]
        Resource = aws_cloudfront_function.routes.arn
      },
      {
        Sid    = "ManageSiteRoles"
        Effect = "Allow"
        Action = [
          "iam:GetRole",
          "iam:ListRoleTags",
          "iam:UpdateRole",
          "iam:UpdateAssumeRolePolicy",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
          "iam:GetRolePolicy",
          "iam:PutRolePolicy",
        ]
        Resource = [aws_iam_role.deploy.arn, aws_iam_role.terraform.arn]
      },
      {
        Sid      = "ManageBudget"
        Effect   = "Allow"
        Action   = ["budgets:ViewBudget", "budgets:ModifyBudget", "budgets:ListTagsForResource", "budgets:TagResource", "budgets:UntagResource"]
        Resource = "arn:aws:budgets::${local.account_id}:budget/${local.budget_name}"
      },
      ], var.manage_github_oidc_provider ? [{
        Sid    = "ManageGitHubOidcProvider"
        Effect = "Allow"
        Action = [
          "iam:GetOpenIDConnectProvider",
          "iam:UpdateOpenIDConnectProviderThumbprint",
          "iam:AddClientIDToOpenIDConnectProvider",
          "iam:RemoveClientIDFromOpenIDConnectProvider",
          "iam:TagOpenIDConnectProvider",
          "iam:UntagOpenIDConnectProvider",
          "iam:ListOpenIDConnectProviderTags",
        ]
        Resource = local.github_oidc_provider_arn
    }] : [])
  })
}
