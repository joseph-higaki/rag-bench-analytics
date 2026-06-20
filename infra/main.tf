# Cheapest-viable AWS skeleton for the cloud milestone. Local dev does NOT need this.
# Each resource carries a one-sentence justification (CLAUDE.md cost discipline). This is
# a starting skeleton: it provisions the durable, stateful pieces (object storage +
# warehouse). Compute (self-hosted Airflow / a scheduled task) is documented in
# infra/README.md and intentionally left to a follow-up milestone rather than shipped
# as untested ECS task defs.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# S3 — the landing zone + marts. Cheapest durable object storage; the whole point of the
# producer/consumer boundary. Versioning on landing so an accidental overwrite is recoverable.
resource "aws_s3_bucket" "landing" {
  bucket = "${var.project}-landing-${var.suffix}"
}

resource "aws_s3_bucket_versioning" "landing" {
  bucket = aws_s3_bucket.landing.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket" "marts" {
  bucket = "${var.project}-marts-${var.suffix}"
}

# The dashboard reads marts Parquet over public-read object URLs from Streamlit Community
# Cloud — keep this bucket's policy as narrow as your sharing model allows. Left default-
# private here; open specific objects/prefixes deliberately rather than the whole bucket.

# RDS Postgres — t4g.micro (free-tier eligible yr 1). The warehouse. Do NOT reach for
# Aurora (CLAUDE.md). Single-AZ, minimal storage; this is a low-frequency analytics DB.
resource "aws_db_instance" "warehouse" {
  identifier            = "${var.project}-pg"
  engine                = "postgres"
  engine_version        = "16"
  instance_class        = "db.t4g.micro"
  allocated_storage     = 20
  storage_type          = "gp3"
  db_name               = var.db_name
  username              = var.db_username
  password              = var.db_password # supply via TF_VAR_db_password / secrets, never commit
  multi_az              = false
  publicly_accessible   = false
  skip_final_snapshot   = true # dev/teardown-friendly; flip for anything you care about
  vpc_security_group_ids = [aws_security_group.warehouse.id]
  tags = { project = var.project }
}

resource "aws_security_group" "warehouse" {
  name        = "${var.project}-pg-sg"
  description = "Postgres access for the analytics pipeline compute only"
  # Ingress intentionally omitted here: add a rule scoped to the compute SG (Airflow
  # host / Fargate task), never 0.0.0.0/0. The dashboard never reaches the DB.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
