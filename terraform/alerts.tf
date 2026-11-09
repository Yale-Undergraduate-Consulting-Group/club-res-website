# Alerts (prod only: the dev box stops every night, so its alarms would be
# noise). Email goes to budget_email; Slack goes through AWS Chatbot once the
# workspace has been authorized in the console and slack_team_id and
# slack_channel_id are set. Cost: one custom metric (app errors, about
# $0.30/month); alarms stay within the free ten.
#
# ponytail: the topics are not KMS-encrypted. Messages carry only alarm names
# and metric values, never client data, and encrypting them would mean granting
# CloudWatch use of the client-data key.
locals {
  alerts       = local.edge
  slack_alerts = local.alerts && var.slack_team_id != "" && var.slack_channel_id != ""
}

resource "aws_sns_topic" "alerts" {
  count = local.alerts ? 1 : 0
  name  = "${local.name}-alerts"
}

# CloudFront metrics exist only in us-east-1, and an alarm can notify only a
# topic in its own region.
resource "aws_sns_topic" "alerts_edge" {
  count    = local.alerts ? 1 : 0
  provider = aws.us_east_1
  name     = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = local.alerts ? 1 : 0
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.budget_email
}

resource "aws_sns_topic_subscription" "alerts_edge_email" {
  count     = local.alerts ? 1 : 0
  provider  = aws.us_east_1
  topic_arn = aws_sns_topic.alerts_edge[0].arn
  protocol  = "email"
  endpoint  = var.budget_email
}

# AWS hardware or host failure: EC2 moves the box to healthy hardware, keeping
# its ID, private IP and volumes.
resource "aws_cloudwatch_metric_alarm" "box_system" {
  count               = local.alerts ? 1 : 0
  alarm_name          = "${local.name}-box-hardware"
  alarm_description   = "The server's AWS host failed its status check; EC2 is recovering it onto new hardware."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.box.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = ["arn:aws:automate:${var.aws_region}:ec2:recover", aws_sns_topic.alerts[0].arn]
  ok_actions          = [aws_sns_topic.alerts[0].arn]
}

# The operating system stopped answering (kernel hang, out of memory): reboot.
resource "aws_cloudwatch_metric_alarm" "box_instance" {
  count               = local.alerts ? 1 : 0
  alarm_name          = "${local.name}-box-unresponsive"
  alarm_description   = "The server failed its own status check for 3 minutes; EC2 is rebooting it."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  dimensions          = { InstanceId = aws_instance.box.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = ["arn:aws:automate:${var.aws_region}:ec2:reboot", aws_sns_topic.alerts[0].arn]
  ok_actions          = [aws_sns_topic.alerts[0].arn]
}

# Uvicorn prefixes failures with "ERROR:"; each traceback adds one more line.
resource "aws_cloudwatch_log_metric_filter" "app_errors" {
  count          = local.alerts ? 1 : 0
  name           = "${local.name}-app-errors"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "?Traceback ?\"ERROR:\""
  metric_transformation {
    name      = "AppErrors"
    namespace = "club-res-website/${var.environment}"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "app_errors" {
  count               = local.alerts ? 1 : 0
  alarm_name          = "${local.name}-app-errors"
  alarm_description   = "The app logged several errors within 5 minutes. Logs: CloudWatch /${local.name}/app."
  namespace           = "club-res-website/${var.environment}"
  metric_name         = "AppErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]
  ok_actions          = [aws_sns_topic.alerts[0].arn]
}

# Members see errors: at least a quarter of requests failed for 10 minutes.
# Fewer than 3 requests in a period never alarm, so one failed request on an
# idle site is not an outage.
resource "aws_cloudwatch_metric_alarm" "site_errors" {
  count               = local.alerts ? 1 : 0
  provider            = aws.us_east_1
  alarm_name          = "${local.name}-site-errors"
  alarm_description   = "At least 25% of site requests returned a server error for 10 minutes."
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 25
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts_edge[0].arn]
  ok_actions          = [aws_sns_topic.alerts_edge[0].arn]

  metric_query {
    id          = "failing"
    expression  = "IF(requests >= 3, rate, 0)"
    label       = "5xx rate when busy"
    return_data = true
  }
  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/CloudFront"
      metric_name = "Requests"
      dimensions  = { DistributionId = aws_cloudfront_distribution.site[0].id, Region = "Global" }
      period      = 300
      stat        = "Sum"
    }
  }
  metric_query {
    id = "rate"
    metric {
      namespace   = "AWS/CloudFront"
      metric_name = "5xxErrorRate"
      dimensions  = { DistributionId = aws_cloudfront_distribution.site[0].id, Region = "Global" }
      period      = 300
      stat        = "Average"
    }
  }
}

# Slack delivery. The role only lets Chatbot post; the deny-all guardrail
# means nobody can run AWS commands from the channel.
resource "aws_iam_role" "chatbot" {
  count                = local.slack_alerts ? 1 : 0
  name                 = "${local.name}-chatbot"
  permissions_boundary = aws_iam_policy.boundary.arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "chatbot.amazonaws.com" }
    }]
  })
}

resource "aws_chatbot_slack_channel_configuration" "alerts" {
  count                 = local.slack_alerts ? 1 : 0
  configuration_name    = "${local.name}-alerts"
  iam_role_arn          = aws_iam_role.chatbot[0].arn
  slack_team_id         = var.slack_team_id
  slack_channel_id      = var.slack_channel_id
  sns_topic_arns        = [aws_sns_topic.alerts[0].arn, aws_sns_topic.alerts_edge[0].arn]
  guardrail_policy_arns = ["arn:aws:iam::aws:policy/AWSDenyAll"]
  logging_level         = "NONE"
}
