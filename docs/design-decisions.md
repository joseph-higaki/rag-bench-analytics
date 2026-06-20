# Design decisions

Lightweight ADR log — one entry per non-obvious architectural decision, terse and
decision-oriented. Newest first. "Accepted (pending)" means decided but not yet
implemented; the implementation checklist is the spec for the later execution batch.

---

## ADR-002 — Cosmos renders dbt models as individual Airflow tasks

- **Status:** Accepted — implemented.
- **Date:** 2026-06-19

### Context

The Airflow DAG ran `dbt build` as a single `BashOperator`. Failures showed one red
box — no visibility into which model or test broke. Cosmos (`astronomer-cosmos`)
parses the dbt project and renders each model/seed/test as its own Airflow task with
correct dependency edges.

### Decision

1. Replace the `BashOperator` dbt step with a `DbtTaskGroup` from Cosmos.
2. dbt is installed in an **isolated virtualenv** inside the Airflow container (avoids
   dependency conflicts). Cosmos uses `ExecutionConfig(dbt_executable_path=...)` to
   call it.
3. Profile config reuses the same `profiles.yml` as `make dbt` — target driven by
   `DBT_TARGET` env var (CLAUDE.md rule #3).
4. The Airflow image is built from `airflow/Dockerfile` (extends the stock image with
   cosmos + dbt-postgres).
5. **Version pinning:** `dbt-postgres` and `astronomer-cosmos` are pinned in both
   `pyproject.toml` (IDE/local) and `airflow/Dockerfile` (runtime). A CI check should
   verify the two stay in sync — not yet implemented.

### Consequences

- (+) Full dbt DAG visible in the Airflow UI; failures pinpoint the exact model/test.
- (+) Same profiles.yml and target mechanism as the local path.
- (−) Dockerfile build is no longer a stock pull — adds ~30s to the first build.
- (−) Duplicate version pins across pyproject.toml and Dockerfile until the CI check
  is added.

### Pending

- [ ] CI check: verify pinned versions in `pyproject.toml` match `airflow/Dockerfile`.

---

## ADR-001 — Self-hosted warehouse + dashboard, direct connection (supersedes the RDS / Community-Cloud defaults)

- **Status:** Accepted — implementation deferred to a later batch.
- **Date:** 2026-06-19

### Context

The data is small (thousands of rows), so Postgres is more than enough as the dbt
transformation engine; no MPP warehouse is warranted. The original CLAUDE.md cost
defaults were **RDS `db.t4g.micro`** + **Streamlit Community Cloud** reading **Parquet
exported to S3** — that combo existed to keep the warehouse private while using *free*
SaaS hosting that lives outside the VPC (so it can't reach a private DB; hence the
file handoff).

We instead want a **fully self-managed stack** (cost/control, and it demonstrates the
end-to-end skill). Self-hosting Streamlit **inside the VPC** removes the only reason for
the Parquet handoff: the dashboard can reach the warehouse privately over the network.

### Decision

1. **Warehouse hosting:** Postgres in a **container, self-hosted on EC2** — *not* RDS.
   Same engine local (docker-compose) and cloud (container on EC2), so dbt models are
   unchanged across environments.
2. **Serving:** **self-hosted Streamlit**, in the **same VPC** as the warehouse,
   connecting **directly to the `*_marts` schema** over a private security-group rule.
   The DB gets **no public ingress**; only the dashboard's own port is exposed.
3. **Parquet/S3 export becomes optional**, kept behind a swap point — not the primary
   serving path. Retain it only if we also want a Community-Cloud fallback or archived
   snapshots.
4. **Dashboard data access behind one swap point:** `load_mart()` in `dashboard/app.py`
   gets two implementations — `postgres` (direct SQL on marts) and `parquet` (read from
   object storage) — selected by `SERVE_MODE` env (default `postgres` for self-hosted).
5. **Marts-only rule preserved:** the dashboard reads the `*_marts` schema (or read-only
   views over it) via a **read-only DB role**, never `raw`/`staging`/`intermediate`.

### Environments

- **dev:** docker-compose on the laptop (postgres + streamlit). Direct `localhost` connection.
- **staging:** single EC2 box running the Postgres container + Streamlit container (+
  ingestion/dbt as a scheduled task). Shared failure domain — acceptable for staging.
- **prod:** separate the concerns — dedicated box (or instance) for Postgres vs. the app;
  exact prod topology TBD. RDS remains a fallback if Postgres ops burden proves too high.

### Consequences

- (+) Full control, no managed-service lock-in, live data (no export lag), fewer moving
  parts in the serve path, and a complete self-managed-stack skill signal.
- (−) **We own Postgres ops:** backups, patching, durability, hardening. Mitigate with
  volume snapshots, a pinned image, a restricted SG, and no public DB port.
- (−) The DB must be up whenever the dashboard is used — **not idle-to-zero** like the
  Parquet/Community-Cloud path. Acceptable since the same EC2 already hosts both.
- (−) Lose the free hosting tier; we pay for the EC2 box(es).
- (−) Single box in dev/staging is a shared failure domain; prod must separate at least
  the DB from the app.

### Implementation checklist (for the execution batch)

- [ ] `dashboard/app.py`: add the `load_mart` swap point (`SERVE_MODE=postgres|parquet`);
      add a `psycopg` reader that selects from the `*_marts` schema.
- [ ] `.env.example`: add `SERVE_MODE` (default `postgres`) and the dashboard's read-only
      DB creds + marts schema name.
- [ ] dbt / SQL: create a **read-only role** for the dashboard with `SELECT` on the marts
      schema only (least privilege); optionally expose read-only views.
- [ ] `docker-compose.yml`: add a `streamlit` service (dev) connecting directly to
      `postgres`; keep the export path optional.
- [ ] `infra/`: replace/supplement the RDS resource with **EC2 + containerized Postgres**
      + security groups — DB SG allows the app SG only; app SG exposes the dashboard port
      to an IP allowlist or an ALB (with auth). Keep the S3 marts bucket only if retaining
      the Parquet fallback.
- [ ] `.claude/CLAUDE.md`: update **Environments** + **Cost discipline** to make
      self-hosted Postgres-on-EC2 + in-VPC direct-connect Streamlit the primary path, and
      demote RDS / Community-Cloud / Parquet to documented alternatives (standing context
      must not contradict this ADR).
- [ ] `README.md`: update the architecture/serving narrative and the cost wording.
- [ ] `Makefile`: note dev (`make dashboard` direct-connect) vs staging/prod run modes.

### Security note

Dashboard connects with a **read-only role** scoped to marts; the DB never gets a public
ingress rule; the app box exposes only the dashboard port, behind an IP allowlist or an
authenticated ALB.
