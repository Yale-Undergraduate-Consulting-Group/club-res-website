# One VPC per environment. The box sits in a public subnet: its public IPv4
# address is for egress only (Bedrock, ECR, Google, crawled sites); no NAT
# gateway. The only HTTP door is CloudFront through the VPC origin, which
# also requires the VPC to have an internet gateway.
locals {
  vpc_cidr = var.environment == "prod" ? "10.30.0.0/16" : "10.20.0.0/16"
  az       = "${var.aws_region}a"
  # The Amazon-provided resolver (VPC base + 2) and Amazon Time Sync.
  vpc_resolver_cidr = "${cidrhost(local.vpc_cidr, 2)}/32"
  time_sync_cidr    = "169.254.169.123/32"
}

resource "aws_vpc" "main" {
  cidr_block           = local.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(local.vpc_cidr, 8, 0)
  availability_zone       = local.az
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The default security group of a new VPC allows all traffic between its
# members; strip it so nothing can use it by accident.
resource "aws_default_security_group" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_security_group" "box" {
  name        = "${local.name}-box"
  description = "Application box: no ingress except the CloudFront VPC origin (edge only)"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-box" }
}

# CloudFront creates this group in the VPC when the VPC origin is deployed.
# depends_on defers the lookup to apply only while the VPC origin changes.
data "aws_security_group" "cloudfront_vpc_origin" {
  count  = local.edge ? 1 : 0
  vpc_id = aws_vpc.main.id
  name   = "CloudFront-VPCOrigins-Service-SG"

  depends_on = [aws_cloudfront_vpc_origin.api]
}

resource "aws_vpc_security_group_ingress_rule" "cloudfront" {
  count                        = local.edge ? 1 : 0
  security_group_id            = aws_security_group.box.id
  description                  = "CloudFront VPC origin only"
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  referenced_security_group_id = data.aws_security_group.cloudfront_vpc_origin[0].id
}

# ponytail: HTTP(S) egress stays open to the internet because the product
# crawls arbitrary company sites and calls Google, Bedrock and other APIs, and
# MX checks resolve arbitrary recipient domains, so a domain allowlist cannot
# work. DNS may only go to the VPC resolver, so DNS Firewall cannot be
# bypassed with an outside resolver; NTP only to Amazon Time Sync.
locals {
  box_egress = {
    http    = { protocol = "tcp", port = 80, cidr = "0.0.0.0/0" }
    https   = { protocol = "tcp", port = 443, cidr = "0.0.0.0/0" }
    dns_udp = { protocol = "udp", port = 53, cidr = local.vpc_resolver_cidr }
    dns_tcp = { protocol = "tcp", port = 53, cidr = local.vpc_resolver_cidr }
    ntp     = { protocol = "udp", port = 123, cidr = local.time_sync_cidr }
  }
}

# trivy:ignore:AVD-AWS-0104 reviewed 2026-10-05: ports 80 and 443 stay open for the reasons above; docs/CI_CD.md section 14 records the limit and the detection controls.
resource "aws_vpc_security_group_egress_rule" "box" {
  for_each          = local.box_egress
  security_group_id = aws_security_group.box.id
  description       = each.key
  ip_protocol       = each.value.protocol
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = each.value.cidr
}

# In-region S3 traffic from the VPC goes through this endpoint, whose policy
# admits only this stack's data buckets and the AWS-owned buckets the box
# needs: ECR image layers, Amazon Linux 2023 packages and SSM Agent assets.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "StackDataBuckets"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:*"
        Resource = flatten([for name in values(local.data_buckets) : [
          "arn:aws:s3:::${name}", "arn:aws:s3:::${name}/*",
        ]])
      },
      {
        Sid       = "AwsOwnedBuckets"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource = [
          "arn:aws:s3:::prod-${var.aws_region}-starport-layer-bucket/*",
          "arn:aws:s3:::al2023-repos-${var.aws_region}-de612dc2/*",
          "arn:aws:s3:::aws-ssm-${var.aws_region}/*",
          "arn:aws:s3:::amazon-ssm-${var.aws_region}/*",
          "arn:aws:s3:::amazon-ssm-packages-${var.aws_region}/*",
          "arn:aws:s3:::${var.aws_region}-birdwatcher-prod/*",
          "arn:aws:s3:::aws-ssm-document-attachments-${var.aws_region}/*",
          "arn:aws:s3:::aws-ssm-distributor-file-${var.aws_region}/*",
          "arn:aws:s3:::patch-baseline-snapshot-${var.aws_region}/*",
        ]
      },
    ]
  })
  tags = { Name = "${local.name}-s3" }
}

# --- Network audit trail ------------------------------------------------------

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/${local.name}/vpc-flow"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.env.arn
}

resource "aws_iam_role" "flow_logs" {
  name                 = "${local.name}-flow-logs"
  permissions_boundary = aws_iam_policy.boundary.arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:ec2:${var.aws_region}:${local.account_id}:vpc-flow-log/*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "write-flow-logs"
  role = aws_iam_role.flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow.arn}:*"
    }]
  })
}

resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow.arn
  iam_role_arn             = aws_iam_role.flow_logs.arn
  max_aggregation_interval = 600
}

resource "aws_cloudwatch_log_group" "dns" {
  name              = "/${local.name}/dns-queries"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.env.arn
}

resource "aws_route53_resolver_query_log_config" "main" {
  name            = local.name
  destination_arn = aws_cloudwatch_log_group.dns.arn
}

resource "aws_route53_resolver_query_log_config_association" "main" {
  resolver_query_log_config_id = aws_route53_resolver_query_log_config.main.id
  resource_id                  = aws_vpc.main.id
}

# --- DNS Firewall ---------------------------------------------------------------
# Blocks lookups of AWS-maintained malware and botnet command-and-control
# domains. A deny-by-default allowlist is impossible here (see box_egress).
# The IDs are the AWS-owned managed lists of us-east-2 (aws_region is pinned);
# verify with: aws route53resolver list-firewall-domain-lists --region us-east-2
locals {
  managed_domain_lists = {
    malware = "rslvr-fdl-19f9b4c730a14748" # AWSManagedDomainsMalwareDomainList
    botnet  = "rslvr-fdl-3c30dcb4c50b4401" # AWSManagedDomainsBotnetCommandandControl
  }
}

resource "aws_route53_resolver_firewall_rule_group" "main" {
  name = local.name
}

resource "aws_route53_resolver_firewall_rule" "managed" {
  for_each                = local.managed_domain_lists
  name                    = "block-${each.key}"
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.main.id
  firewall_domain_list_id = each.value
  action                  = "BLOCK"
  block_response          = "NODATA"
  priority                = each.key == "malware" ? 100 : 200
}

resource "aws_route53_resolver_firewall_rule_group_association" "main" {
  name                   = local.name
  firewall_rule_group_id = aws_route53_resolver_firewall_rule_group.main.id
  vpc_id                 = aws_vpc.main.id
  priority               = 101
}

# Fail closed: if DNS Firewall cannot evaluate a query, the query is refused.
resource "aws_route53_resolver_firewall_config" "main" {
  resource_id        = aws_vpc.main.id
  firewall_fail_open = "DISABLED"
}
