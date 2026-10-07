# Client data at rest: one customer-managed key per environment encrypts the
# SQLite volume, the catalog and backup buckets, the app secret and the log
# groups. Deleting the key makes all of them unreadable, hence the long window.
resource "aws_kms_key" "env" {
  description             = "${local.name} client data"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountIamPolicies"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogsForThisStack"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/${local.name}/*" }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "env" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.env.key_id
}

locals {
  data_buckets = {
    catalog   = "${local.name}-catalog-${local.account_id}"
    backups   = "${local.name}-backups-${local.account_id}"
    documents = "${local.name}-documents-${local.account_id}"
  }
  # Prefixes whose current objects expire after 30 days, per bucket.
  # catalog: exports and discovery files are temporary working copies.
  # backups: one snapshot set per day, 30 days of restore points.
  # documents: member uploads; no current-object expiry.
  expiring_prefixes = {
    catalog   = ["exports/", "discovery/"]
    backups   = ["sqlite/"]
    documents = []
  }
  # The browser origin: CloudFront with the edge, the SSM port forward without.
  # It depends on the distribution, so the box's first-boot copy omits it.
  public_url = local.edge ? "https://${aws_cloudfront_distribution.site[0].domain_name}" : "http://localhost:8000"
  data_object_actions = [
    "s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectAttributes", "s3:GetObjectTagging",
    "s3:PutObject", "s3:PutObjectTagging", "s3:DeleteObject", "s3:DeleteObjectVersion",
    "s3:RestoreObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
  ]
}

# catalog: prospect workbooks, discovery and export files read by the app.
# backups: verified SQLite .backup snapshots uploaded by the box's daily timer.
# documents: workspace files that browsers upload and download through
# presigned URLs signed by the box role (backend/app/routers/workspace.py).
resource "aws_s3_bucket" "data" {
  for_each      = local.data_buckets
  bucket        = each.value
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_ownership_controls" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each                = aws_s3_bucket.data
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.env.arn
    }
    # One KMS call per object prefix instead of per request.
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}

# Lifecycle expiry is asynchronous eligibility, not an immediate-deletion promise.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
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
  dynamic "rule" {
    for_each = local.expiring_prefixes[each.key]
    content {
      id     = "expire-${trimsuffix(rule.value, "/")}"
      status = "Enabled"
      filter { prefix = rule.value }
      expiration { days = 30 }
    }
  }
  depends_on = [aws_s3_bucket_versioning.data]
}

# TLS only, and object data only through this VPC's S3 endpoint (the box).
# Bucket configuration calls stay with IAM, so Terraform and operators can
# manage the bucket; catalog_operator_principal_arns lists the roles allowed
# to move objects from outside the VPC (workbook upload, backup restore).
# The documents bucket has its own policy below: browsers reach it directly.
resource "aws_s3_bucket_policy" "data" {
  for_each = { for key, bucket in aws_s3_bucket.data : key => bucket if key != "documents" }
  bucket   = each.value.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [each.value.arn, "${each.value.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "ObjectsOnlyThroughThisVpc"
        Effect    = "Deny"
        Principal = "*"
        Action    = local.data_object_actions
        Resource  = "${each.value.arn}/*"
        Condition = merge(
          { StringNotEquals = { "aws:sourceVpce" = aws_vpc_endpoint.s3.id } },
          length(var.catalog_operator_principal_arns) > 0 ? { ArnNotLike = { "aws:PrincipalArn" = var.catalog_operator_principal_arns } } : {},
        )
      },
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.data]
}

# Browser requests with presigned URLs never pass the VPC endpoint, but they
# execute as the role that signed them. Only the box role (and listed
# operator roles) may touch document objects, from anywhere, over TLS.
resource "aws_s3_bucket_policy" "documents" {
  bucket = aws_s3_bucket.data["documents"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.data["documents"].arn, "${aws_s3_bucket.data["documents"].arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "ObjectsOnlyForTheBoxRole"
        Effect    = "Deny"
        Principal = "*"
        Action    = local.data_object_actions
        Resource  = "${aws_s3_bucket.data["documents"].arn}/*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = concat([aws_iam_role.box.arn], var.catalog_operator_principal_arns) } }
      },
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.data]
}

resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.data["documents"].id
  cors_rule {
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = [local.public_url]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 300
  }
}

# Values are seeded out of band by scripts/seed-secrets.sh, so no secret value
# ever enters Terraform state or a saved plan. The box reads it at each deploy.
resource "aws_secretsmanager_secret" "app" {
  name                    = "club-res-website/${var.environment}/app"
  description             = "Container secrets for ${local.name}; seeded by scripts/seed-secrets.sh."
  kms_key_id              = aws_kms_key.env.arn
  recovery_window_in_days = 30
}

locals {
  bedrock_model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
  config_parameter = "/club-res-website/${var.environment}/config"
  # Non-secret runtime configuration read by ops/restart-yucg.sh on every
  # deploy; the key list is documented in templates/user-data.sh.tftpl.
  # public_url is added per copy: only the SSM copy may name the distribution,
  # which depends on the box (see compute.tf).
  box_config = {
    env                    = var.environment
    region                 = var.aws_region
    secret_arn             = aws_secretsmanager_secret.app.arn
    catalog_bucket         = aws_s3_bucket.data["catalog"].bucket
    backups_bucket         = aws_s3_bucket.data["backups"].bucket
    documents_bucket       = aws_s3_bucket.data["documents"].bucket
    log_group              = aws_cloudwatch_log_group.app.name
    bedrock_model_id       = local.bedrock_model_id
    email_delivery_enabled = var.environment == "prod"
    edge                   = local.edge
    config_parameter       = local.config_parameter
  }
}

resource "aws_ssm_parameter" "config" {
  name        = local.config_parameter
  description = "Non-secret runtime configuration of ${local.name}; ops/restart-yucg.sh writes it to /etc/yucg/config.json."
  type        = "String"
  value = jsonencode(merge(local.box_config, {
    public_url = local.public_url
  }))
}
