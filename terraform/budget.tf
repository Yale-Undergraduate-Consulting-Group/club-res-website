# Monthly cost alert for this site environment. The filter counts only
# resources carrying this environment's default "Site" tag, so dev and
# prod are tracked apart even in a shared account. Prerequisite: activate
# "Site" once per account under Billing > Cost allocation tags (it appears
# about a day after the first tagged resource exists); until then AWS cannot
# attribute spend to the tag and this budget reports zero.
resource "aws_budgets_budget" "monthly" {
  name         = local.budget_name
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Site$%s", local.name)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
