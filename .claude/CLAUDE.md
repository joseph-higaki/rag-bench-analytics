# CLAUDE.md — rag-bench-analytics

Standing context for any Claude Code session in this repo. Keep it current; it is
read every session, so it should be dense and high-signal, not exhaustive.

> **Design decisions live in `docs/design-decisions.md` (ADR log).** When this file
> conflicts with an accepted ADR, the ADR wins — reflect it here promptly. Serving +
> warehouse-hosting topology is governed by **ADR-001** (self-hosted, direct-connect).

## What this repo is

The **analytics consumer** for `biomedical-rag-bench`. The benchmark *produces*
evaluation results; this repo *turns accumulated results into a dimensional model
and a dashboard*. It is a deliberately standalone, publishable data-engineering
artifact.

Producer → consumer boundary (do not blur it):

- This repo's input is **`run.json`** result files (plus `questions.jsonl` and
  corpus-profile JSON) landed in **object storage (S3 / local MinIO)**.
- This repo **never imports from, reaches into, or depends on the benchmark repo's
  code.** It starts at the files in object storage. If you need a field that isn't
  in `run.json`, that's a change request to the benchmark, not a workaround here.

## Golden rules (the things to get right)

1. **`run.json` is an external, append-only, versioned contract owned by the
   benchmark.** The staging layer must *validate* it and *tolerate unknown keys*.
   Never assume a frozen schema; never edit source files.
2. **Streamlit reads marts only**, never raw or intermediate tables — via a read-only
   role on the marts schema (direct SQL), or the optional Parquet export.
3. **Same dbt models everywhere.** Local vs cloud differ only by dbt *target* and
   environment variables — not by separate model code.
4. **Local must run fully offline and reproducibly**: `docker compose up` + sample
   fixtures → the whole pipeline runs with no AWS account.
5. **Secrets via env only** (`.env`, never committed; `.env.example` documents
   them). No credentials in code, dbt models, or DAGs.
6. **Cost discipline is a feature.** Every cloud component must justify itself in
   one sentence; default to the cheapest viable option (see Cost section).
7. **Ground-truth semantics carried in from the benchmark:** ground truth is graph
   traversal, never an LLM; the generator is fixed per run; the compared variable is
   the retriever. Don't let a model name, column comment, or dashboard label imply
   otherwise.

## Architecture / dataflow

```
S3 / MinIO            ingestion            Postgres (raw)        dbt                      marts            dashboard
run.json        →   extract + load    →   raw.* (json/text)  →  staging → intermediate → fct/dim   →   Streamlit (direct, in-VPC)
questions.jsonl                                                  (flatten, conform,        (star)        └ Parquet→S3 optional fallback
corpus_profile                                                   explode traversal_info)
                                  ── orchestrated by Airflow ──
```

- **Extract/Load (EL):** pull files from object storage, land them in a `raw`
  schema in Postgres as-is (JSON/text). No transformation here.
- **Transform (dbt):** staging → intermediate → marts. This is where the schema
  morph happens.
- **Serve:** self-hosted Streamlit reads the **marts schema directly** (in-VPC,
  read-only role). Exporting marts to Parquet in S3 is an *optional* fallback (e.g.
  for Streamlit Community Cloud). See ADR-001.
- **Orchestrate:** Airflow DAG runs extract → load → `dbt build` → (optional) export →
  cache refresh.

## Repo structure

```
rag-bench-analytics/
├── README.md                  # public narrative + the architecture diagram
├── CLAUDE.md                  # this file
├── docker-compose.yml         # local stack: postgres, airflow, minio (local S3)
├── .env.example               # documents every required env var
├── pyproject.toml             # python deps (airflow, dbt-postgres, streamlit, boto3, ...)
├── Makefile                   # one-word entrypoints (see Commands)
├── _resources/                # diagrams (drawio, png) — mirrors emr_data_pipeline
│   └── architecture.drawio
├── ingestion/                 # EL: object storage -> raw Postgres
│   ├── extract.py             # list + download run.json / corpus profiles
│   └── load_raw.py            # land into raw schema (idempotent, keyed by run_id)
├── dbt/                       # the transformation project (the heart of the repo)
│   ├── dbt_project.yml
│   ├── profiles.example.yml   # local + cloud targets, env-driven
│   ├── models/
│   │   ├── staging/           # stg_*  : 1:1 with sources; flatten, rename, cast,
│   │   │                      #          EXPLODE traversal_info (branch per mechanism)
│   │   ├── intermediate/      # int_*  : joins (questions, corpus), dedup
│   │   └── marts/             # fct_scored_answer + dim_* (the star schema)
│   ├── seeds/                 # static lookups (condition labels, scoring types)
│   ├── macros/
│   └── (schema.yml per layer) # sources, freshness, tests, model contracts
├── airflow/
│   └── dags/
│       └── analytics_pipeline.py
├── dashboard/
│   └── app.py                 # Streamlit; reads marts (Parquet/Postgres) only
├── infra/                     # IaC for the cheapest-AWS deploy (terraform)
└── tests/                     # python unit tests for ingestion
```

Deliberate drift from a typical `emr_data_pipeline` layout (challenge as needed):
keep `_resources/` for diagrams, but enforce a strict **dbt staging/intermediate/
marts** split and keep **ingestion, orchestration, transformation, and serving in
separate top-level folders**. If the older repo co-mingled orchestration with
transformation or used a flat models dir, do not copy that here.

## Source contract (what arrives in `run.json`)

One record per scored answer, at grain **(run/generator, question, retriever
condition)**. Indicative shape — the benchmark owns the authoritative keys and may
add more:

```jsonc
{
  "run_id": "...", "generator_model": "...",      // fixed per run
  "question_id": "...", "retriever": "graph_sparqlgen",
  "score": 1.0, "verdict": "pass",
  "latency_ms": 1240, "context_tokens": 850,
  "sources": [ ... ],
  "traversal_info": {                              // POLYMORPHIC — keys vary by mechanism
    "mechanism": "sparqlgen",
    "hop_count": 2,                                // graph_neighborhood
    "writer_model": "...", "writer_input_tokens": 120,
    "writer_output_tokens": 80, "sparql": "...", "sparql_valid": true
  }
}
```

Also consumed and joined at transform time: `questions.jsonl` (question `type`,
`hop_count`, seeds, ground truth, `template_id`) keyed by `question_id`; corpus
profile (`corpus_build_id`, `ttl_sha256`, counts) referenced by the run manifest.

## Target model (the star) and the schema morph

Grain of the fact: **one scored answer = run × question × condition.**

- `fct_scored_answer` — FKs to all dims + measures: `score`, `verdict`,
  `latency_ms`, `context_tokens`, and the *exploded* `traversal_info` measures
  (`hop_count`, `writer_tokens` = input+output, `sparql_valid`). Sparse columns are
  expected and acceptable (null where a mechanism doesn't produce them).
- `dim_question` (type, hop_count, template_id), `dim_retriever_cond`
  (null/vector/graph_neighborhood/graph_sparqlgen, plus `mechanism`/`writer_model`
  as attributes), `dim_generator` (model, temperature), `dim_judge` (scoring_type),
  `dim_corpus` (`corpus_build_id`, scale, sha, counts), `dim_run` (run_id, seed,
  code_tag, dataset_ver, timestamp).

Where each layer does the work:

- **staging** flattens `run.json` and *explodes* `traversal_info` with
  mechanism-aware branching (this branch is the real transform — `traversal_info`
  is schema-on-read). Cast, rename, validate. One staging model per source.
- **intermediate** joins question attributes and corpus profile, dedups.
- **marts** builds the conformed star above.

Routing reference (the morph): top-level ids → FKs; `score/verdict/latency/tokens`
and exploded numerics → fact measures; `mechanism`/`writer_model` → dim attributes;
`sparql` text / `sources` / `endpoint` → kept in raw provenance, **dropped from the
star**.

## dbt conventions

- Naming: `stg_`, `int_`, `fct_`, `dim_`. Sources declared in `schema.yml` with
  **freshness** checks on the S3-landed raw.
- Tests on every layer: `not_null`/`unique` on keys, `accepted_values` on
  `verdict`/`mechanism`/`scoring_type`, relationships from fact → dims.
- Use **model contracts** on marts so column types are enforced — the dashboard
  depends on them.
- Idempotent loads keyed by `run_id`; transforms must be re-runnable.

## Environments

**Local (default, offline, reproducible):** `docker-compose` brings up Postgres,
Airflow (LocalExecutor), and **MinIO** as S3. Sample `run.json` fixtures seed
MinIO. `make pipeline` runs the whole chain with no AWS account.

**Cloud (self-hosted, per ADR-001):** real S3 for the landing zone; **Postgres
self-hosted as a container on EC2** (RDS `db.t4g.micro` is the low-ops *fallback*);
Airflow **self-hosted** on a small instance / ECS Fargate; **Streamlit self-hosted in
the same VPC**, connecting directly to the marts via a read-only role (DB has no public
ingress). Same dbt models; the cloud target is selected by env var.

## Cost discipline (cheapest AWS)

- **S3** — cheap object storage; the landing zone. Fine.
- **Postgres** — **self-hosted container on EC2** (default, ADR-001); RDS `t4g.micro`
  (free tier yr 1) is the low-ops fallback. Do not reach for Aurora.
- **Airflow** — **DO NOT use MWAA** (~$350/mo floor). Self-host on a single
  `t4g.small` (EC2 or Fargate). For this low frequency, a scheduled task or cron
  could replace it entirely; Airflow is kept deliberately for the skill, not
  because the cadence needs it.
- **Dashboard** — **self-hosted Streamlit in-VPC** (default, ADR-001), connecting
  directly to the marts via a read-only role; the DB has no public ingress, only the
  dashboard port is exposed (IP allowlist / authenticated ALB). **Streamlit Community
  Cloud (free) reading Parquet exported to S3** is the documented fallback for when the
  dashboard can't sit in the VPC. Trade-off: the self-hosted path is not idle-to-zero
  (the box stays up); accepted because it also hosts the warehouse.
- Tear-down friendliness: prefer components that scale/cost to ~zero when idle.

## Commands (Makefile)

- `make up` — start the local stack (postgres, airflow, minio).
- `make seed` — load sample `run.json` fixtures into MinIO.
- `make ingest` — extract + load raw.
- `make dbt` — `dbt build` (run + test).
- `make export` — marts → Parquet in (Min)IO.
- `make dashboard` — run Streamlit locally.
- `make pipeline` — the full chain end to end (the offline reproducibility check).

## Working agreement for Claude Code

- You have full repo context once it exists — **be opinionated**. If a request is
  wrong or not worth the churn, say so.
- **Gate destructive and infra-affecting changes** behind a short plan: schema
  migrations, file moves, Terraform applies, anything touching credentials.
- **Don't add a tool or service without a one-sentence justification** that isn't
  "to show I know it." This repo's signal is a clean contract and restraint, not
  tool count.
- Keep the producer/consumer boundary intact: never couple back to the benchmark
  repo's internals.
