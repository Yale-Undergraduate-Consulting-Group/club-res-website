# Edge (prod by default): one CloudFront distribution serves the Vite build
# from the private site bucket and sends /api to the box through a VPC
# origin. The box has no internet-facing listener.
locals {
  site_origin_id = "site-bucket"
  api_origin_id  = "box-api"
  # AWS managed policies: Managed-CachingOptimized, Managed-CachingDisabled,
  # Managed-AllViewerExceptHostHeader.
  caching_optimized_policy_id      = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  caching_disabled_policy_id       = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  all_viewer_except_host_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
  # Vite SPA with same-origin /api; Google sign-in is a top-level redirect
  # from /api/auth/google; Lato comes from Google Fonts (frontend/src/index.css).
  # Document uploads PUT straight to the documents bucket with a presigned URL
  # (regional or global S3 host, depending on how boto3 signs it). Only the
  # bucket name is used here, so the distribution never depends on the bucket.
  content_security_policy = join("; ", [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "connect-src 'self' https://${local.data_buckets["documents"]}.s3.${var.aws_region}.amazonaws.com https://${local.data_buckets["documents"]}.s3.amazonaws.com",
    "form-action 'self' https://accounts.google.com",
    "upgrade-insecure-requests",
  ])
}

resource "aws_cloudfront_origin_access_control" "site" {
  count                             = local.edge ? 1 : 0
  name                              = local.name
  description                       = "CloudFront signs every request to the private site bucket."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# SPA routing for the static behavior only: a path whose last segment has no
# file extension is a client-side route and gets /index.html. /api never runs
# this function (it has its own behaviors); the guard keeps it that way.
# Routing here instead of 403/404 custom error responses keeps real API
# status codes intact, because error responses apply to every origin.
resource "aws_cloudfront_function" "routes" {
  count   = local.edge ? 1 : 0
  name    = "${local.name}-routes"
  runtime = "cloudfront-js-2.0"
  comment = "SPA routes"
  publish = true
  code    = <<-JS
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri === '/api' || uri.indexOf('/api/') === 0) return request;
  if (uri.substring(uri.lastIndexOf('/') + 1).indexOf('.') === -1) request.uri = '/index.html';
  return request;
}
JS
}

resource "aws_cloudfront_response_headers_policy" "site" {
  count   = local.edge ? 1 : 0
  name    = "${local.name}-security-headers"
  comment = "HSTS, CSP and anti-framing for the SPA and /api"
  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    content_security_policy {
      content_security_policy = local.content_security_policy
      override                = true
    }
  }
}

resource "aws_cloudfront_vpc_origin" "api" {
  count = local.edge ? 1 : 0
  vpc_origin_endpoint_config {
    name                   = "${local.name}-api"
    arn                    = aws_instance.box.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  count               = local.edge ? 1 : 0
  enabled             = true
  comment             = "${local.name} app"
  default_root_object = "index.html"
  is_ipv6_enabled     = true
  http_version        = "http2and3"
  price_class         = "PriceClass_100"
  web_acl_id          = var.enable_waf ? aws_wafv2_web_acl.edge[0].arn : null

  # No custom domain yet: the site answers on its *.cloudfront.net name with the
  # default certificate. A domain is a later change: an ACM certificate in
  # us-east-1, aliases, and viewer_certificate with TLSv1.2_2021.
  aliases = []

  # Apply returns once CloudFront accepts the change, so a plan/apply job stays
  # within its timeout; CloudFront finishes propagation on its own.
  wait_for_deployment = false

  origin {
    origin_id                = local.site_origin_id
    domain_name              = aws_s3_bucket.site[0].bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site[0].id
  }

  # The box answers HTTP on port 80 inside the VPC only.
  origin {
    origin_id   = local.api_origin_id
    domain_name = aws_instance.box.private_dns
    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.api[0].id
      origin_read_timeout      = var.origin_read_timeout
      origin_keepalive_timeout = 5
    }
  }

  default_cache_behavior {
    target_origin_id           = local.site_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = local.caching_optimized_policy_id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site[0].id
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.routes[0].arn
    }
  }

  dynamic "ordered_cache_behavior" {
    for_each = ["/api", "/api/*"]
    content {
      path_pattern               = ordered_cache_behavior.value
      target_origin_id           = local.api_origin_id
      viewer_protocol_policy     = "redirect-to-https"
      allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods             = ["GET", "HEAD"]
      compress                   = true
      cache_policy_id            = local.caching_disabled_policy_id
      origin_request_policy_id   = local.all_viewer_except_host_policy_id
      response_headers_policy_id = aws_cloudfront_response_headers_policy.site[0].id
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate { cloudfront_default_certificate = true }
}

resource "aws_s3_bucket_policy" "site" {
  count  = local.edge ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudFrontReadsThroughOriginAccessControl"
        Effect    = "Allow"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.site[0].arn}/*"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site[0].arn } }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.site[0].arn, "${aws_s3_bucket.site[0].arn}/*"]
        Principal = "*"
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.site]
}

# WAF for a CloudFront distribution lives in us-east-1 (scope CLOUDFRONT).
resource "aws_wafv2_web_acl" "edge" {
  count    = local.edge && var.enable_waf ? 1 : 0
  provider = aws.us_east_1
  name     = local.name
  scope    = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "aws-common"
    priority = 10
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
        # Imports and Studio drafts post JSON bodies above the rule's 8 KB
        # limit; count instead of block so legitimate requests pass.
        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-aws-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 20
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-aws-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # Per client IP, 2000 requests in any 5-minute window (about 7 per second),
  # far above one member's use, low enough to blunt scripted floods.
  rule {
    name     = "rate-per-ip"
    priority = 30
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-rate-per-ip"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = local.name
    sampled_requests_enabled   = true
  }
}
