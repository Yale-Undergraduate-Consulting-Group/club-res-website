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
  # Every bucket this stack owns; the site bucket exists only with the edge.
  stack_bucket_arns = concat([for b in aws_s3_bucket.site : b.arn], [for b in aws_s3_bucket.data : b.arn])
  # The box, selected by its Site tag, which default_tags puts on every resource.
  site_tag_condition = { StringEquals = { "aws:ResourceTag/Site" = local.name } }
  # dev and prod share one account; each environment's roles are fenced off
  # from the other's resources by the boundary below.
  other_environment = var.environment == "dev" ? "prod" : "dev"
  other_name        = "club-res-website-${local.other_environment}"
  # IAM rejects a wildcard service in a policy ARN, so the boundary names every
  # service this stack uses. Global services take an empty region field.
  boundary_regional_services = ["secretsmanager", "ssm", "ecr", "logs", "kms", "ec2", "wafv2", "scheduler", "dlm", "route53resolver"]
  boundary_global_services   = ["cloudfront", "budgets"]
}

# Permissions boundary on every role of this stack. A role's effective rights are
# the intersection of its own policies and this boundary, so even a reviewed apply
# that rewrites a role's inline policy (ManageStackRoles allows that) cannot reach
# the other environment. The roles cannot edit or remove the boundary; changing it
# needs operator credentials. Verified with iam simulate-custom-policy against both
# environments' resource ARNs (secrets, parameters, state paths, tagged KMS/EC2/CloudFront).
resource "aws_iam_policy" "boundary" {
  name        = "${local.name}-boundary"
  description = "Caps every ${local.name} role: no access to ${local.other_name} resources."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "AllowWithinIdentityGrants", Effect = "Allow", Action = "*", Resource = "*" },
      {
        Sid    = "DenyOtherEnvironmentByName"
        Effect = "Deny"
        Action = "*"
        # Validated with a real IAM CreatePolicy, then checked with iam
        # simulate-custom-policy against both environments' ARNs.
        Resource = concat(
          flatten([for s in local.boundary_regional_services : [
            "arn:aws:${s}:*:*:*${local.other_name}*",
            "arn:aws:${s}:*:*:*club-res-website/${local.other_environment}*",
          ]]),
          flatten([for s in local.boundary_global_services : [
            "arn:aws:${s}::*:*${local.other_name}*",
            "arn:aws:${s}::*:*club-res-website/${local.other_environment}*",
          ]]),
          [for t in ["role", "policy", "instance-profile"] : "arn:aws:iam::*:${t}/${local.other_name}*"],
          [
            "arn:aws:s3:::${local.other_name}*",
            "arn:aws:s3:::*/club-res-website/${local.other_environment}/*",
            "arn:aws:s3:::*/ci-plans/${local.other_environment}/*",
            "arn:aws:s3:::*/ci-diagnostics/${local.other_environment}/*",
          ],
        )
      },
      {
        Sid       = "DenyOtherEnvironmentByTag"
        Effect    = "Deny"
        Action    = "*"
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/Site" = local.other_name } }
      },
      {
        Sid       = "DenyTaggingAsOtherEnvironment"
        Effect    = "Deny"
        Action    = "*"
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestTag/Site" = local.other_name } }
      },
      {
        Sid    = "DenyBoundaryRemoval"
        Effect = "Deny"
        Action = [
          "iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary", "iam:CreatePolicyVersion",
          "iam:DeletePolicy", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.manage_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates GitHub tokens against its trusted CA library; the IAM API
  # still accepts these published GitHub thumbprints.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

# Ships a release: pushes the image to this environment's ECR repository,
# runs ops/restart-yucg.sh on this environment's box through SSM and, with
# the edge, publishes the SPA to the site bucket and invalidates CloudFront.
resource "aws_iam_role" "deploy" {
  name                 = "${local.name}-deploy"
  description          = "GitHub environment ${var.environment} ships the app image, restarts the box and publishes the SPA."
  max_session_duration = 3600
  permissions_boundary = aws_iam_policy.boundary.arn
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
  name = "ship-app"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "EcrToken"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "PushAppImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Sid      = "RunShellScriptDocument"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
      },
      {
        Sid       = "RunOnlyOnThisEnvironmentsBox"
        Effect    = "Allow"
        Action    = "ssm:SendCommand"
        Resource  = "arn:aws:ec2:${var.aws_region}:${local.account_id}:instance/*"
        Condition = { StringEquals = { "ssm:resourceTag/Site" = local.name } }
      },
      {
        Sid      = "ReadCommandResultsAndPreflight"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ec2:DescribeInstances", "ec2:DescribeSecurityGroups", "ec2:DescribeVolumes"]
        Resource = "*"
      },
      # Statements in a conditional group share one shape (list Action and
      # Resource) so Terraform can unify the two branches' types.
      ], local.edge ? [
      {
        Sid      = "ListSiteBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.site[0].arn]
      },
      {
        Sid      = "PublishSiteObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = ["${aws_s3_bucket.site[0].arn}/*"]
      },
      {
        Sid      = "InvalidateSiteCache"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
        Resource = [aws_cloudfront_distribution.site[0].arn]
      },
    ] : [])
  })
}

# Runs reviewed plan/apply from prod. It reads every resource of this state
# and updates their settings; it cannot create or delete them. The first
# apply (bootstrap) and any change that creates, replaces or deletes a
# resource run with operator credentials, and the apply environment requires
# a reviewer.
resource "aws_iam_role" "terraform" {
  name                 = "${local.name}-terraform"
  description          = "GitHub environments infrastructure-${var.environment}-plan/apply run reviewed Terraform."
  max_session_duration = 3600
  permissions_boundary = aws_iam_policy.boundary.arn
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
        # Refresh: read-only calls, most of which have no resource-level scope.
        # Secret values and parameter values other than the AMI and the
        # config are deliberately absent.
        Sid    = "ReadStack"
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "kms:DescribeKey",
          "kms:GetKeyPolicy",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicyTags",
          "iam:ListPolicyVersions",
          "kms:GetKeyRotationStatus",
          "kms:ListAliases",
          "kms:ListResourceTags",
          "logs:DescribeLogGroups",
          "logs:ListTagsForResource",
          "logs:ListTagsLogGroup",
          "iam:GetInstanceProfile",
          "route53resolver:Get*",
          "route53resolver:List*",
          "dlm:GetLifecyclePolicy",
          "dlm:GetLifecyclePolicies",
          "dlm:ListTagsForResource",
          "ecr:DescribeRepositories",
          "ecr:GetLifecyclePolicy",
          "ecr:GetRepositoryPolicy",
          "ecr:ListTagsForResource",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetResourcePolicy",
          "ssm:DescribeParameters",
          "ssm:ListTagsForResource",
          "scheduler:GetSchedule",
          "scheduler:ListTagsForResource",
          "cloudfront:GetVpcOrigin",
          "cloudfront:GetResponseHeadersPolicy",
          "cloudfront:GetResponseHeadersPolicyConfig",
          "wafv2:GetWebACL",
          "wafv2:ListTagsForResource",
        ]
        Resource = "*"
      },
      {
        Sid    = "ReadAmiAndConfigParameters"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}::parameter/aws/service/ami-amazon-linux-latest/*",
          "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${local.config_parameter}",
        ]
      },
      {
        Sid    = "ManageStackBuckets"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketAcl",
          "s3:GetBucketCORS",
          "s3:PutBucketCORS",
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
        Resource = local.stack_bucket_arns
      },
      {
        # Instance type and volume size changes stop/start the box or grow
        # the volume; tag edits and endpoint/security-group rule settings.
        Sid    = "UpdateTaggedNetworkAndCompute"
        Effect = "Allow"
        Action = [
          "ec2:ModifyInstanceAttribute",
          "ec2:StopInstances",
          "ec2:StartInstances",
          "ec2:ModifyVolume",
          "ec2:ModifyVpcEndpoint",
          "ec2:ModifySecurityGroupRules",
          "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
          "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
          "ec2:CreateTags",
          "ec2:DeleteTags",
        ]
        Resource  = "*"
        Condition = local.site_tag_condition
      },
      {
        # Starting the box with CMK-encrypted volumes creates EBS grants.
        Sid    = "ManageEnvironmentKey"
        Effect = "Allow"
        Action = [
          "kms:CreateGrant",
          "kms:PutKeyPolicy",
          "kms:UpdateKeyDescription",
          "kms:EnableKeyRotation",
          "kms:TagResource",
          "kms:UntagResource",
          "kms:UpdateAlias",
        ]
        Resource = [aws_kms_key.env.arn, aws_kms_alias.env.arn]
      },
      {
        Sid      = "ManageLogGroups"
        Effect   = "Allow"
        Action   = ["logs:PutRetentionPolicy", "logs:AssociateKmsKey", "logs:TagResource", "logs:UntagResource", "logs:TagLogGroup", "logs:UntagLogGroup"]
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/${local.name}/*"
      },
      {
        Sid      = "ManageAppSecretMetadata"
        Effect   = "Allow"
        Action   = ["secretsmanager:UpdateSecret", "secretsmanager:TagResource", "secretsmanager:UntagResource"]
        Resource = aws_secretsmanager_secret.app.arn
      },
      {
        Sid      = "ManageConfigParameter"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter", "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource"]
        Resource = "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${local.config_parameter}"
      },
      {
        Sid      = "ManageAppRepository"
        Effect   = "Allow"
        Action   = ["ecr:PutLifecyclePolicy", "ecr:PutImageScanningConfiguration", "ecr:PutImageTagMutability", "ecr:TagResource", "ecr:UntagResource"]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Sid      = "ManageSnapshotPolicy"
        Effect   = "Allow"
        Action   = ["dlm:UpdateLifecyclePolicy", "dlm:TagResource", "dlm:UntagResource"]
        Resource = aws_dlm_lifecycle_policy.data.arn
      },
      {
        Sid    = "ManageDnsFirewallAndQueryLogs"
        Effect = "Allow"
        Action = [
          "route53resolver:UpdateFirewallRule",
          "route53resolver:UpdateFirewallRuleGroupAssociation",
          "route53resolver:UpdateFirewallConfig",
          "route53resolver:TagResource",
          "route53resolver:UntagResource",
        ]
        Resource = "arn:aws:route53resolver:${var.aws_region}:${local.account_id}:*"
      },
      {
        Sid    = "ManageStackRoles"
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
          "iam:ListInstanceProfilesForRole",
          "iam:GetRolePolicy",
          "iam:PutRolePolicy",
        ]
        Resource = concat(
          [aws_iam_role.deploy.arn, aws_iam_role.terraform.arn, aws_iam_role.box.arn, aws_iam_role.dlm.arn, aws_iam_role.flow_logs.arn],
          [for r in aws_iam_role.scheduler : r.arn],
        )
      },
      {
        Sid       = "PassServiceRoles"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = concat([aws_iam_role.dlm.arn, aws_iam_role.flow_logs.arn], [for r in aws_iam_role.scheduler : r.arn])
        Condition = { StringEquals = { "iam:PassedToService" = ["dlm.amazonaws.com", "vpc-flow-logs.amazonaws.com", "scheduler.amazonaws.com"] } }
      },
      {
        Sid      = "ManageBudget"
        Effect   = "Allow"
        Action   = ["budgets:ViewBudget", "budgets:ModifyBudget", "budgets:ListTagsForResource", "budgets:TagResource", "budgets:UntagResource"]
        Resource = "arn:aws:budgets::${local.account_id}:budget/${local.budget_name}"
      },
      ], var.office_hours_enabled ? [{
        Sid      = "ManageOfficeHoursSchedules"
        Effect   = "Allow"
        Action   = ["scheduler:UpdateSchedule", "scheduler:TagResource", "scheduler:UntagResource"]
        Resource = "arn:aws:scheduler:${var.aws_region}:${local.account_id}:schedule/default/${local.name}-*"
      }] : [], local.edge ? [
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
        Resource = [aws_cloudfront_distribution.site[0].arn]
      },
      {
        Sid      = "ManageOriginAccessControl"
        Effect   = "Allow"
        Action   = ["cloudfront:GetOriginAccessControl", "cloudfront:UpdateOriginAccessControl"]
        Resource = ["arn:aws:cloudfront::${local.account_id}:origin-access-control/${aws_cloudfront_origin_access_control.site[0].id}"]
      },
      {
        Sid      = "ManageRoutesFunction"
        Effect   = "Allow"
        Action   = ["cloudfront:DescribeFunction", "cloudfront:GetFunction", "cloudfront:UpdateFunction", "cloudfront:PublishFunction"]
        Resource = [aws_cloudfront_function.routes[0].arn]
      },
      {
        Sid      = "ManageVpcOriginAndHeaders"
        Effect   = "Allow"
        Action   = ["cloudfront:UpdateVpcOrigin", "cloudfront:UpdateResponseHeadersPolicy", "cloudfront:ListTagsForResource", "cloudfront:TagResource", "cloudfront:UntagResource"]
        Resource = [aws_cloudfront_vpc_origin.api[0].arn, "arn:aws:cloudfront::${local.account_id}:response-headers-policy/${aws_cloudfront_response_headers_policy.site[0].id}"]
      },
      ] : [], local.edge && var.enable_waf ? [{
        Sid      = "ManageWebAcl"
        Effect   = "Allow"
        Action   = ["wafv2:UpdateWebACL", "wafv2:TagResource", "wafv2:UntagResource"]
        Resource = aws_wafv2_web_acl.edge[0].arn
        }] : [], var.manage_github_oidc_provider ? [{
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
