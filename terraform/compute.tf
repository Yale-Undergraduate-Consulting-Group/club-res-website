# One box per environment runs the FastAPI + SPA container from
# docker/app.Dockerfile; SQLite lives on a separate retained volume at /data.
# Access is SSM Session Manager only: no SSH, no key pair.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  bedrock_inference_profile = "arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/${local.bedrock_model_id}"
  # The US cross-region inference profile routes to any of these regions.
  bedrock_foundation_models = [
    for region in ["us-east-1", "us-east-2", "us-west-2"] :
    "arn:aws:bedrock:${region}::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
  ]
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/${local.name}/app"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.env.arn
}

resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last 10 tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_iam_role" "box" {
  name = "${local.name}-box"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "box_ssm" {
  role       = aws_iam_role.box.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "box" {
  name = "run-app"
  role = aws_iam_role.box.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrToken"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid      = "PullAppImage"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Sid      = "ReadAppSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = aws_secretsmanager_secret.app.arn
      },
      {
        Sid      = "ReadConfig"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${local.config_parameter}"
      },
      {
        Sid      = "UseEnvironmentKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = aws_kms_key.env.arn
      },
      {
        Sid      = "ListDataBuckets"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [for b in aws_s3_bucket.data : b.arn]
      },
      {
        Sid      = "ReadWriteDataObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = [for b in aws_s3_bucket.data : "${b.arn}/*"]
      },
      {
        Sid      = "ContainerLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "${aws_cloudwatch_log_group.app.arn}:*"
      },
      {
        Sid      = "InvokeHaiku"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = concat([local.bedrock_inference_profile], local.bedrock_foundation_models)
      },
    ]
  })
}

resource "aws_iam_instance_profile" "box" {
  name = "${local.name}-box"
  role = aws_iam_role.box.name
}

resource "aws_ebs_volume" "data" {
  availability_zone = local.az
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true
  kms_key_id        = aws_kms_key.env.arn
  tags              = { Name = "${local.name}-data" }
  lifecycle { prevent_destroy = true }
}

resource "aws_instance" "box" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.box.id]
  iam_instance_profile        = aws_iam_instance_profile.box.name
  associate_public_ip_address = true
  disable_api_termination     = var.environment == "prod"
  monitoring                  = false
  user_data_replace_on_change = false
  user_data = templatefile("${path.module}/templates/user-data.sh.tftpl", {
    name               = local.name
    data_volume_serial = replace(aws_ebs_volume.data.id, "-", "")
    # The first-boot copy cannot name the distribution, which is created after
    # the box; restart-yucg.sh replaces it with the SSM copy on every deploy.
    config_json = jsonencode(merge(local.box_config, { public_url = local.edge ? "" : "http://localhost:8000" }))
  })

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # 2 hops: the container reaches the instance role through IMDSv2.
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    kms_key_id            = aws_kms_key.env.arn
    delete_on_termination = true
  }

  tags = { Name = local.name }

  # A new AMI or edited first-boot script must never replace the box: it
  # holds the attached SQLite volume and the CloudFront VPC origin.
  lifecycle { ignore_changes = [ami, user_data] }
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/xvdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.box.id
  stop_instance_before_detaching = true
}

# --- Daily EBS snapshots of the data volume (crash-consistent; the app-level
# .backup snapshots in the backups bucket are the verified restore path).

resource "aws_iam_role" "dlm" {
  name = "${local.name}-dlm"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "dlm.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

# The data volume uses the environment CMK; snapshots of it need these grants.
resource "aws_iam_role_policy" "dlm" {
  name = "snapshot-encrypted-volume"
  role = aws_iam_role.dlm.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "GrantsForEbs"
        Effect    = "Allow"
        Action    = "kms:CreateGrant"
        Resource  = aws_kms_key.env.arn
        Condition = { Bool = { "kms:GrantIsForAWSResource" = "true" } }
      },
      {
        Sid      = "UseEnvironmentKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:ReEncrypt*"]
        Resource = aws_kms_key.env.arn
      },
    ]
  })
}

resource "aws_dlm_lifecycle_policy" "data" {
  description        = "${local.name} daily data volume snapshots"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"
  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Name = aws_ebs_volume.data.tags["Name"] }
    schedule {
      name      = "daily"
      copy_tags = true
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["05:00"]
      }
      retain_rule { count = 7 }
    }
  }
}

# --- On/off ------------------------------------------------------------------------
# Off = stop the instance; the volumes stay. Manual control uses plain EC2 calls
# (terraform/README.md). office_hours_enabled adds a weekday schedule.

resource "aws_iam_role" "scheduler" {
  count = var.office_hours_enabled ? 1 : 0
  name  = "${local.name}-office-hours"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  count = var.office_hours_enabled ? 1 : 0
  name  = "start-stop-box"
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StartStopThisBox"
        Effect   = "Allow"
        Action   = ["ec2:StartInstances", "ec2:StopInstances"]
        Resource = aws_instance.box.arn
      },
      {
        # Starting an instance with CMK-encrypted volumes creates EBS grants.
        Sid       = "AttachEncryptedVolumes"
        Effect    = "Allow"
        Action    = "kms:CreateGrant"
        Resource  = aws_kms_key.env.arn
        Condition = { Bool = { "kms:GrantIsForAWSResource" = "true" } }
      },
    ]
  })
}

locals {
  office_hours = {
    start = { action = "startInstances", cron = "cron(0 12 ? * MON-FRI *)" }
    stop  = { action = "stopInstances", cron = "cron(0 4 ? * TUE-SAT *)" }
  }
}

resource "aws_scheduler_schedule" "office_hours" {
  for_each                     = { for k, v in local.office_hours : k => v if var.office_hours_enabled }
  name                         = "${local.name}-${each.key}"
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "UTC"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:${each.value.action}"
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({ InstanceIds = [aws_instance.box.id] })
  }
}
