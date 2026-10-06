terraform {
  required_version = ">= 1.14, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  # scripts/terraform-release.sh supplies bucket, key and region with
  # -backend-config at init. State and saved plans never enter Git.
  backend "s3" { use_lockfile = true }
}

locals {
  default_tags = {
    Application = "club-res-website"
    Environment = var.environment
    ManagedBy   = "terraform"
    Site        = local.name
  }
}

provider "aws" {
  region = var.aws_region
  default_tags { tags = local.default_tags }
}

# CloudFront-scope WAF web ACLs exist only in us-east-1, which the org
# guardrail still allows for WAF. Nothing regional is created here.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  default_tags { tags = local.default_tags }
}

variable "environment" {
  type        = string
  description = "Delivery environment and branch of this state: dev or prod."
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "github_repository" {
  type        = string
  default     = "Yale-Undergraduate-Consulting-Group/club-res-website"
  description = "owner/name of the repository whose GitHub environments may assume the roles."
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be owner/name."
  }
}

variable "aws_region" {
  type        = string
  default     = "us-east-2"
  description = "Region of every regional resource. The organization guardrail denies EC2, S3 and ECR elsewhere."
  validation {
    condition     = var.aws_region == "us-east-2"
    error_message = "aws_region must be us-east-2: the organization guardrail denies regional services elsewhere, and the DNS Firewall managed list IDs in network.tf are us-east-2 IDs."
  }
}

variable "monthly_budget_usd" {
  type        = number
  description = "Monthly spend for this site environment that triggers budget alerts."
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}

variable "budget_email" {
  type        = string
  description = "Address that receives budget alerts."
  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_email))
    error_message = "budget_email must be an email address."
  }
}

variable "tf_state_bucket" {
  type        = string
  description = "Bucket that holds this state, its lock file and the saved CI plans. Scopes the Terraform role."
}

variable "tf_state_key" {
  type        = string
  description = "Object key of this state in tf_state_bucket. Scopes the Terraform role."
}

variable "manage_github_oidc_provider" {
  type        = bool
  default     = true
  description = "IAM allows one token.actions.githubusercontent.com provider per account. Set false when the account already has it (scripts/bootstrap-account.sh creates it); the roles then trust the existing provider."
}

variable "enable_edge" {
  type        = bool
  default     = null
  nullable    = true
  description = "CloudFront, the site bucket and the box's port-80 listener for the VPC origin. Null means true in prod and false in dev, where developers reach the container through SSM port forwarding."
}

variable "enable_waf" {
  type        = bool
  default     = true
  description = "Attach a WAF web ACL (AWS managed rules and a per-IP rate limit) to CloudFront. Ignored without the edge."
}

variable "origin_read_timeout" {
  type        = number
  default     = 60
  description = "Seconds CloudFront waits for the box on /api. Above 60 needs an approved Service Quotas increase for the CloudFront origin response timeout first, or apply fails."
  validation {
    condition     = var.origin_read_timeout >= 30 && var.origin_read_timeout <= 120 && floor(var.origin_read_timeout) == var.origin_read_timeout
    error_message = "origin_read_timeout must be a whole number of seconds from 30 to 120."
  }
}

variable "instance_type" {
  type        = string
  default     = "t3.small"
  description = "x86_64 instance type of the application box. Changing it stops and starts the box."
  validation {
    condition     = can(regex("^(t3|t3a|m5|m6i|m7i|c6i|c7i)\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must be an x86_64 type from the t3, t3a, m5, m6i, m7i, c6i or c7i families (the AMI is AL2023 x86_64)."
  }
}

variable "data_volume_gb" {
  type        = number
  default     = 8
  description = "Size of the retained SQLite data volume in GiB. EBS volumes can grow but never shrink; grow the xfs filesystem on the box afterwards (xfs_growfs /data)."
  validation {
    condition     = var.data_volume_gb >= 8 && var.data_volume_gb <= 1024 && floor(var.data_volume_gb) == var.data_volume_gb
    error_message = "data_volume_gb must be a whole number from 8 to 1024."
  }
}

variable "office_hours_enabled" {
  type        = bool
  default     = false
  description = "Start the box weekdays 12:00 UTC and stop it 04:00 UTC the next day. Mail jobs run only while the box is on."
}

variable "catalog_operator_principal_arns" {
  type        = list(string)
  default     = []
  description = "IAM role ARNs (wildcards allowed) that may read or write catalog, backup and document objects directly, for example the operator role that uploads prospect workbooks or restores a backup. Otherwise catalog and backup objects are reachable only through this VPC's S3 endpoint, and document objects only by the box role (including the presigned URLs it signs)."
  validation {
    condition     = alltrue([for arn in var.catalog_operator_principal_arns : can(regex("^arn:aws:iam::[0-9]{12}:role/.+$", arn))])
    error_message = "catalog_operator_principal_arns must contain IAM role ARNs."
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  name        = "club-res-website-${var.environment}"
  site_bucket = "${local.name}-site-${local.account_id}"
  edge        = coalesce(var.enable_edge, var.environment == "prod")
}

# Private origin for the Vite build (frontend/dist). Only CloudFront reads it
# (see the bucket policy in cloudfront.tf); only the deploy role writes it.
# Without the edge (dev) the container serves the SPA itself.
resource "aws_s3_bucket" "site" {
  count         = local.edge ? 1 : 0
  bucket        = local.site_bucket
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_ownership_controls" "site" {
  count  = local.edge ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "site" {
  count                   = local.edge ? 1 : 0
  bucket                  = aws_s3_bucket.site[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  count  = local.edge ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_versioning" "site" {
  count  = local.edge ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  versioning_configuration { status = "Enabled" }
}

# Versioning keeps every replaced page for rollback; keep 30 days of history.
resource "aws_s3_bucket_lifecycle_configuration" "site" {
  count  = local.edge ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter { prefix = "" }
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
  rule {
    id     = "remove-expired-delete-markers"
    status = "Enabled"
    filter { prefix = "" }
    expiration { expired_object_delete_marker = true }
  }
  depends_on = [aws_s3_bucket_versioning.site]
}
