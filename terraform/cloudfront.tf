locals {
  site_origin_id = "site-bucket"
  # AWS managed policies: Managed-CachingOptimized and Managed-SecurityHeadersPolicy.
  caching_optimized_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  security_headers_policy_id  = "67f7725c-6f97-4210-82d7-5512b31e9d03"
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = local.name
  description                       = "CloudFront signs every request to the private site bucket."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Maps clean URLs onto the files of a Next.js static export (output: 'export',
# default trailingSlash: false): "/" and "/docs/" -> index.html, "/about" ->
# "/about.html". Paths with a file extension pass through unchanged.
resource "aws_cloudfront_function" "routes" {
  name    = "${local.name}-routes"
  runtime = "cloudfront-js-2.0"
  comment = "Static export routes"
  publish = true
  code    = <<-JS
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.charAt(uri.length - 1) === '/') {
    request.uri = uri + 'index.html';
  } else if (uri.substring(uri.lastIndexOf('/') + 1).indexOf('.') === -1) {
    request.uri = uri + '.html';
  }
  return request;
}
JS
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "${local.name} static site"
  default_root_object = "index.html"
  is_ipv6_enabled     = true
  http_version        = "http2and3"
  price_class         = "PriceClass_100"

  # No custom domain yet: the site answers on its *.cloudfront.net name with the
  # default certificate. A domain is a later change: an ACM certificate in
  # us-east-1, aliases, and viewer_certificate with TLSv1.2_2021.
  aliases = []

  # Apply returns once CloudFront accepts the change, so a plan/apply job stays
  # within its timeout; CloudFront finishes propagation on its own.
  wait_for_deployment = false

  origin {
    origin_id                = local.site_origin_id
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = local.site_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = local.caching_optimized_policy_id
    response_headers_policy_id = local.security_headers_policy_id
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.routes.arn
    }
  }

  # Without s3:ListBucket, S3 answers 403 for a missing key; both become the
  # export's 404 page.
  custom_error_response {
    error_code            = 403
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 60
  }
  custom_error_response {
    error_code            = 404
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 60
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate { cloudfront_default_certificate = true }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudFrontReadsThroughOriginAccessControl"
        Effect    = "Allow"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.site.arn}/*"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn } }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.site.arn, "${aws_s3_bucket.site.arn}/*"]
        Principal = "*"
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.site]
}
