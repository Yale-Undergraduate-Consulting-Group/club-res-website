variable "aws_region" {
  type        = string
  default     = "us-east-2"
  description = "Workload region of the vended accounts; the guardrail SCP allows regional services only there."
  validation {
    condition     = var.aws_region == "us-east-2"
    error_message = "aws_region must be us-east-2: the guardrail SCP denies regional services elsewhere."
  }
}

variable "accounts" {
  type = map(object({
    name  = string
    email = string
  }))
  description = "Member accounts keyed by environment, e.g. { dev = { name = \"YUCG_Dev\", email = \"...\" }, prod = { ... } }. Each email must be unique across AWS."
  validation {
    condition     = length(var.accounts) > 0 && alltrue([for k in keys(var.accounts) : contains(["dev", "prod"], k)])
    error_message = "accounts keys must be dev and/or prod."
  }
  validation {
    condition     = alltrue([for a in values(var.accounts) : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", a.email))])
    error_message = "Every account email must be a valid address."
  }
  validation {
    condition     = alltrue([for a in values(var.accounts) : length(trimspace(a.name)) > 0])
    error_message = "Every account needs a name."
  }
}

variable "parent_id" {
  type        = string
  default     = null
  description = "Parent of the YUCG OU (root or OU id). Null uses the organization root."
  validation {
    condition     = var.parent_id == null || can(regex("^(r-[0-9a-z]{4,32}|ou-[0-9a-z]{4,32}-[a-z0-9]{8,32})$", var.parent_id))
    error_message = "parent_id must be a root (r-...) or OU (ou-...) id."
  }
}

variable "existing_ou_id" {
  type        = string
  default     = null
  description = "Reuse this OU instead of creating YUCG; parent_id is then ignored."
  validation {
    condition     = var.existing_ou_id == null || can(regex("^ou-[0-9a-z]{4,32}-[a-z0-9]{8,32}$", var.existing_ou_id))
    error_message = "existing_ou_id must be an OU id (ou-...)."
  }
}

variable "attach_guardrails" {
  type        = bool
  default     = true
  description = "Attach the YUCG-guardrails SCP to the OU."
}
