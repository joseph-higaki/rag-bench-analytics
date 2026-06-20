# infra — cheapest-viable AWS deploy

This skeleton provisions only the **durable, stateful** pieces. Compute is documented,
not auto-provisioned, to keep the module small and honest about what's been tested.

## What Terraform provisions here

| Resource | Why (one sentence) |
|---|---|
| S3 `…-landing` | Object-storage landing zone for `run.json`/`.jsonl` + manifests (the producer/consumer boundary). |
| S3 `…-marts`   | Parquet marts the dashboard reads — no live DB connection from the internet. |
| RDS `db.t4g.micro` Postgres | The warehouse; free-tier eligible year 1, single-AZ — **not** Aurora. |
| Security group | Scopes Postgres to the compute SG only; the dashboard never reaches the DB. |

```bash
export TF_VAR_db_password=...        # from a secrets backend, never committed
terraform init
terraform apply -var suffix=<your-unique-suffix>
```

Then point the cloud dbt target at the outputs:

```bash
export DBT_TARGET=cloud
export POSTGRES_HOST=$(terraform output -raw warehouse_endpoint | sed 's/:5432//')
export S3_ENDPOINT_URL=                # unset => real AWS S3
export S3_LANDING_BUCKET=$(terraform output -raw landing_bucket)
export S3_MARTS_BUCKET=$(terraform output -raw marts_bucket)
```

The **same dbt models** run; only the target + env change (CLAUDE.md rule #3).

## Compute (follow-up milestone — deliberately not auto-provisioned)

- **Orchestration:** self-host Airflow on a single `t4g.small` (EC2 or ECS Fargate).
  **Do NOT use MWAA** (~$350/mo floor). At this cadence a scheduled Fargate task or
  cron running `make ingest && make dbt && make export` replaces Airflow entirely —
  Airflow is kept locally for the skill, not because the cadence needs it.
- **Dashboard:** Streamlit Community Cloud (free), reading the marts Parquet from S3.
- **Tear-down friendliness:** S3 + a stopped/`t4g.micro` RDS cost ~zero idle; destroy
  the RDS instance between demos and re-`apply` when needed.
