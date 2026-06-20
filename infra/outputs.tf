output "landing_bucket" {
  value = aws_s3_bucket.landing.bucket
}

output "marts_bucket" {
  value = aws_s3_bucket.marts.bucket
}

output "warehouse_endpoint" {
  value       = aws_db_instance.warehouse.endpoint
  description = "Set POSTGRES_HOST to this (strip the :5432) for the cloud dbt target."
}
