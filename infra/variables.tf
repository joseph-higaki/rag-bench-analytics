variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "rag-bench-analytics"
}

variable "suffix" {
  type        = string
  description = "Globally-unique suffix for S3 bucket names (e.g. your account id or initials)."
}

variable "db_name" {
  type    = string
  default = "analytics"
}

variable "db_username" {
  type    = string
  default = "analytics"
}

variable "db_password" {
  type      = string
  sensitive = true
  # Supply via TF_VAR_db_password or a secrets backend. Never commit a default.
}
