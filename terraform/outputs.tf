output "site_bucket" {
  value       = aws_s3_bucket.site.bucket
  description = "GitHub environment variable SITE_BUCKET."
}

output "distribution_id" {
  value       = aws_cloudfront_distribution.site.id
  description = "GitHub environment variable CLOUDFRONT_DISTRIBUTION_ID."
}

output "site_url" {
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
  description = "GitHub environment variable SITE_URL."
}

output "deploy_role_arn" {
  value       = aws_iam_role.deploy.arn
  description = "GitHub environment variable AWS_DEPLOY_ROLE_ARN."
}

output "terraform_role_arn" {
  value       = aws_iam_role.terraform.arn
  description = "GitHub environment variable AWS_TERRAFORM_ROLE_ARN on infrastructure-<environment>-plan and -apply."
}
