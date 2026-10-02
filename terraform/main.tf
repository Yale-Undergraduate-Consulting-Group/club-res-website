terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  # scripts/terraform-release.sh supplies bucket, key and region with
  # -backend-config at init. State and saved plans never enter Git.
  backend "s3" { use_lockfile = true }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Application = "club-res-website"
      Environment = var.environment
      ManagedBy   = "terraform"
      Site        = local.name
    }
  }
}

variable "environment" {
  type        = string
  description = "Delivery environment of this state: beta (feature branch) or production (main branch)."
  validation {
    condition     = contains(["beta", "production"], var.environment)
    error_message = "environment must be beta or production."
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
  type    = string
  default = "us-east-1"
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
  description = "IAM allows one token.actions.githubusercontent.com provider per account. Set false when the account already has it (for example, the second environment in a shared account); the roles then trust the existing provider."
}

data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  name        = "club-res-website-${var.environment}"
  site_bucket = "${local.name}-site-${local.account_id}"
}

# Private origin for the static export. Only CloudFront reads it (see the
# bucket policy in cloudfront.tf); only the deploy role writes it.
resource "aws_s3_bucket" "site" {
  bucket        = local.site_bucket
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration { status = "Enabled" }
}

# Versioning keeps every replaced page for rollback; keep 30 days of history.
resource "aws_s3_bucket_lifecycle_configuration" "site" {
  bucket = aws_s3_bucket.site.id
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
