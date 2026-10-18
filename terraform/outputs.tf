output "aws_region" {
  value       = var.aws_region
  description = "GitHub environment variable AWS_REGION."
}

output "aws_account_id" {
  value       = local.account_id
  description = "GitHub environment variable AWS_ACCOUNT_ID."
}

output "deploy_role_arn" {
  value       = aws_iam_role.deploy.arn
  description = "GitHub environment variable AWS_DEPLOY_ROLE_ARN."
}

output "ecr_repository" {
  value       = aws_ecr_repository.app.name
  description = "GitHub environment variable ECR_REPOSITORY."
}

output "instance_id" {
  value       = aws_instance.box.id
  description = "GitHub environment variable BOX_INSTANCE_ID."
}

output "site_bucket" {
  value       = local.edge ? aws_s3_bucket.site[0].bucket : ""
  description = "GitHub environment variable SITE_BUCKET; empty without the edge (dev)."
}

output "distribution_id" {
  value       = local.edge ? aws_cloudfront_distribution.site[0].id : ""
  description = "GitHub environment variable CLOUDFRONT_DISTRIBUTION_ID; empty without the edge (dev)."
}

output "site_url" {
  value       = local.edge ? "https://${aws_cloudfront_distribution.site[0].domain_name}" : ""
  description = "GitHub environment variable SITE_URL; empty without the edge (dev). Google OAuth redirect: <site_url>/api/auth/google/callback."
}

output "terraform_role_arn" {
  value       = aws_iam_role.terraform.arn
  description = "GitHub environment variable AWS_TERRAFORM_ROLE_ARN on infrastructure-<environment>-plan and -apply."
}

output "instance_role_arn" {
  value       = aws_iam_role.box.arn
  description = "Role of the application box."
}

output "catalog_bucket" {
  value       = aws_s3_bucket.data["catalog"].bucket
  description = "Client catalog bucket (workbooks, discovery, exports)."
}

output "backups_bucket" {
  value       = aws_s3_bucket.data["backups"].bucket
  description = "SQLite backup bucket."
}

output "documents_bucket" {
  value       = aws_s3_bucket.data["documents"].bucket
  description = "Workspace documents bucket; browsers use presigned URLs signed by the box role."
}

output "kms_key_arn" {
  value       = aws_kms_key.env.arn
  description = "Customer-managed key of this environment's client data."
}

output "app_secret_arn" {
  value       = aws_secretsmanager_secret.app.arn
  description = "Container secret; seed it with scripts/seed-secrets.sh <environment>."
}
