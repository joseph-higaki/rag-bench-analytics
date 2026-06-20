# rag-bench-analytics

The **analytics consumer** for [`biomedical-rag-bench`](../biomedical-rag-bench). The
benchmark *produces* evaluation results; this repo *turns accumulated results into a
dimensional model and a dashboard*.

> **Scope & coupling.** The pipeline machinery (extract/load, the star schema, dbt,
> serving) is domain-agnostic, but this repo is **purpose-built for `biomedical-rag-bench`**:
> it's bound to that benchmark's output *contract* (the run-file shape, `traversal_info`
> mechanisms) and ships seeds specific to it (retriever families, the hetionet/question
> taxonomy). It consumes those files from object storage and **never imports the benchmark's
> code** — the coupling is to the contract and the domain, not the internals.

The benchmark compares **retrievers** for biomedical question answering: the generator
LLM is fixed per run and ground truth comes from graph traversal (never an LLM), so the
**compared variable is the retriever** (closed-book / vector / graph-neighborhood /
graph-SPARQL-gen). This repo answers: *which retriever wins, at what cost, at what
latency, on which question types?*

## Architecture

```
S3 / MinIO            ingestion          Postgres (raw)        dbt                          marts          dashboard
run files       →   extract + load   →   raw.* (JSONB)    →  staging → intermediate → fct/dim   →   Parquet in S3 → Streamlit
 .jsonl  +                                                   (flatten, conform,           (star +        (reads marts
 .manifest.json                                              EXPLODE traversal_info)       cost)          only)
 questions.jsonl
                                  ── orchestrated by Airflow (optional) ──
```

- **Extract/Load** (`ingestion/`): pull run files from object storage, land them in a
  `raw` schema as JSONB, as-is. Idempotent, keyed by `run_id`. No transformation.
- **Transform** (`dbt/`): `staging → intermediate → marts`. The schema morph lives here.
- **Serve** (`serve/`, `dashboard/`): export marts to Parquet in S3; Streamlit reads the
  Parquet, never the warehouse.
- **Orchestrate** (`airflow/`): a DAG runs the same chain. Optional — `make pipeline`
  runs it without Airflow.

## The source contract (what actually arrives)

The benchmark lands **one file pair per run** plus a shared question bank:

| File | Grain | Notes |
|---|---|---|
| `<run_id>.manifest.json` | one per run | generator, judge, corpus, timestamp |
| `<run_id>.jsonl` | one line per (run, question) | the scored answer + polymorphic `traversal_info` |
| `questions.jsonl` | shared | question type, hop-count, ground truth, template |

`traversal_info` is **schema-on-read**: its keys vary by retrieval mechanism (`dense`,
`neighborhood`, `sparqlgen`) and is empty `{}` on closed-book and older/error records.
The contract is **append-only and versioned** — the staging layer validates it and
tolerates unknown keys; it never assumes a frozen schema.

### Source contract, visualized

Two renderings of the same three input files — kept side by side for now so we can pick
one. Entity names map to files: `RUN_MANIFEST` = `<run_id>.manifest.json`,
`SCORED_ANSWER` = `<run_id>.jsonl`, `QUESTION` = `questions.jsonl`.

**Option A — entity-relationship.** `traversal_info` is shown as one wide, sparse entity;
the note on each attribute marks which `mechanism` populates it (exactly what
`stg_traversal` flattens it into). ER can't draw subtypes, so the polymorphism is implied
by the sparsity notes:

```mermaid
erDiagram
    RUN_MANIFEST ||--o{ SCORED_ANSWER : "run_id"
    QUESTION ||--o{ SCORED_ANSWER : "question_id"
    SCORED_ANSWER ||--|| TRAVERSAL_INFO : "embedded 1:1"

    RUN_MANIFEST {
        text run_id PK
        timestamptz timestamp
        text retriever
        text generator_provider
        text generator_model_resolved
        numeric generator_temperature
        text judge
        text corpus_build_id
        text harness_version
        int num_questions
    }
    SCORED_ANSWER {
        text run_id FK
        text question_id FK
        text retriever
        text scoring
        numeric score
        boolean passed
        boolean judged
        text verdict
        text error
        bigint input_tokens
        bigint output_tokens
        bigint cache_read_input_tokens
        bigint cache_creation_input_tokens
        int num_sources
        numeric retrieval_latency_ms
        numeric generation_latency_ms
        array sources
        object traversal_info
    }
    QUESTION {
        text question_id PK
        text type_id
        text template_id
        text scoring
        text answer_var
        array seeds
        json ground_truth
    }
    TRAVERSAL_INFO {
        text mechanism "dense neighborhood sparqlgen none"
        int top_k "dense"
        int num_chunks "dense"
        text embed_model "dense"
        int hops "neighborhood"
        int num_triples "neighborhood"
        int num_linked "neighborhood"
        text writer_model "sparqlgen"
        bigint writer_input_tokens "sparqlgen"
        bigint writer_output_tokens "sparqlgen"
        boolean sparql_valid "sparqlgen"
        int num_rows "sparqlgen"
    }
```

**Option B — class / inheritance.** `traversal_info` is a base type with one subtype per
`mechanism` — closer to how the JSON actually varies on disk (schema-on-read), at the cost
of a busier diagram:

```mermaid
classDiagram
    direction LR

    class RunManifest["run_id.manifest.json"] {
        <<one per run>>
        text run_id
        timestamptz timestamp
        text retriever
        text generator_provider
        text generator_model
        text generator_model_resolved
        numeric generator_temperature
        text judge
        text corpus_build_id
        text harness_version
        int num_questions
    }

    class ScoredAnswer["run_id.jsonl"] {
        <<one line per run x question>>
        text run_id
        text question_id
        text retriever
        text scoring
        numeric score
        boolean passed
        boolean judged
        text verdict
        text error
        bigint input_tokens
        bigint output_tokens
        bigint cache_read_input_tokens
        bigint cache_creation_input_tokens
        int num_sources
        numeric retrieval_latency_ms
        numeric generation_latency_ms
        array sources
    }

    class Question["questions.jsonl"] {
        <<shared bank, one per question>>
        text question_id
        text type_id
        text template_id
        text scoring
        text answer_var
        array seeds
        json ground_truth
    }

    class TraversalInfo["traversal_info (embedded)"] {
        <<schema-on-read>>
        text mechanism
    }
    class dense {
        <<retriever vector>>
        int top_k
        int num_chunks
        text embed_model
    }
    class neighborhood {
        <<retriever graph_neighborhood>>
        int hops
        int num_triples
        int num_linked
    }
    class sparqlgen {
        <<retriever graph_sparqlgen>>
        text writer_model
        bigint writer_input_tokens
        bigint writer_output_tokens
        text sparql
        boolean sparql_valid
        int num_rows
    }
    class none {
        <<retriever closed_book>>
        empty
    }

    RunManifest "1" --> "N" ScoredAnswer : run_id
    ScoredAnswer "N" --> "1" Question : question_id
    ScoredAnswer *-- TraversalInfo : traversal_info
    TraversalInfo <|-- dense
    TraversalInfo <|-- neighborhood
    TraversalInfo <|-- sparqlgen
    TraversalInfo <|-- none
```

## The star schema

Grain of the fact: **one scored answer = run × question × retriever condition.**

Six conformed dimensions around one fact. FKs are hashed surrogate keys computed with the
*same* column lists in fact and dim, so they join exactly. Measures are abridged here; the
full contracted column list is in `dbt/models/marts/_marts.yml`.

```mermaid
erDiagram
    DIM_RUN ||--o{ FCT_SCORED_ANSWER : run_sk
    DIM_QUESTION ||--o{ FCT_SCORED_ANSWER : question_sk
    DIM_RETRIEVER_COND ||--o{ FCT_SCORED_ANSWER : retriever_cond_sk
    DIM_GENERATOR ||--o{ FCT_SCORED_ANSWER : generator_sk
    DIM_JUDGE ||--o{ FCT_SCORED_ANSWER : judge_sk
    DIM_CORPUS ||--o{ FCT_SCORED_ANSWER : corpus_sk

    FCT_SCORED_ANSWER {
        text scored_answer_sk PK "grain run x question"
        text run_id "degenerate"
        text question_id "degenerate"
        text writer_model "degenerate"
        text run_sk FK
        text question_sk FK
        text retriever_cond_sk FK
        text generator_sk FK
        text judge_sk FK
        text corpus_sk FK
        numeric score
        boolean passed
        integer is_pass
        boolean is_error
        bigint total_tokens
        numeric total_latency_ms
        integer neighborhood_hops "sparse"
        numeric writer_temperature "sparse"
        bigint writer_tokens "sparse"
        boolean sparql_valid "sparse"
        numeric generator_cost_usd
        numeric writer_cost_usd
        numeric total_cost_usd
    }

    DIM_RUN {
        text run_sk PK
        text run_id
        timestamptz run_ts
        text judge
        text harness_version
    }
    DIM_QUESTION {
        text question_sk PK
        text question_id
        text type_id
        text template_id
        integer hop_count
        integer num_seeds
    }
    DIM_RETRIEVER_COND {
        text retriever_cond_sk PK
        text retriever "compared variable"
        text mechanism
        integer neighborhood_hops
        text retriever_family
        boolean is_graph
        text display_label
    }
    DIM_GENERATOR {
        text generator_sk PK
        text generator_provider
        text generator_model_resolved
        numeric generator_temperature
        boolean is_local
    }
    DIM_JUDGE {
        text judge_sk PK
        text scoring
        text scoring_family
        text display_label
    }
    DIM_CORPUS {
        text corpus_sk PK
        text corpus_build_id
        text corpus_scale
        text corpus_sha
    }
```

- `fct_scored_answer` — FKs to all dims + measures: `score`, `passed`, latencies, token
  counts, the *exploded* traversal measures (`neighborhood_hops`, `writer_tokens`,
  `sparql_valid`, …), and **cost** (`generator_cost_usd`, `writer_cost_usd`,
  `total_cost_usd`). Sparse columns are expected (null where a mechanism doesn't produce
  them). The marts contract enforces column types — the dashboard binds to them.
- Dimensions: `dim_run`, `dim_question`, `dim_retriever_cond` (the compared variable),
  `dim_generator`, `dim_judge`, `dim_corpus`.
- The **cost-per-token** join is an *external* seed (`seed_model_pricing.csv`) — the
  prices are maintained here, not produced by the benchmark. Cost = tokens × price for
  both the answering LLM and the SPARQL-writer LLM; local (Ollama) models cost $0.

### The schema morph

The transform lives in staging. Note the fan-out: `raw.scored_answer` feeds **two** staging
models — the top-level flatten (`stg_scored_answers`) and the `traversal_info` explode
(`stg_traversal`) — which rejoin in intermediate alongside the external pricing seed:

```mermaid
flowchart LR
    subgraph S3["object storage (S3 / MinIO)"]
        f1["run_id.manifest.json"]
        f2["run_id.jsonl"]
        f3["questions.jsonl"]
    end

    subgraph RAW["raw schema - JSONB, as-landed (ingestion)"]
        r1[raw.run_manifest]
        r2[raw.scored_answer]
        r3[raw.question]
    end

    subgraph STG["staging - flatten, cast, EXPLODE"]
        s1[stg_runs]
        s2[stg_scored_answers]
        s3[stg_traversal]
        s4[stg_questions]
    end

    SEED[["seed_model_pricing<br/>external, user-maintained"]]
    INT[int_scored_answers_enriched]
    M["marts star<br/>fct + 6 dims"]

    f1 --> r1
    f2 --> r2
    f3 --> r3

    r1 --> s1
    r2 --> s2
    r2 -->|"EXPLODE traversal_info<br/>mechanism-aware branch"| s3
    r3 --> s4

    s1 --> INT
    s2 --> INT
    s3 --> INT
    s4 --> INT
    SEED -->|"cost = tokens x price"| INT

    INT --> M
```

The field-level routing:

| `run.json` field | Lands as |
|---|---|
| top-level ids (`run_id`, `question_id`, `retriever`, …) | dimension FKs |
| `score` / `passed` / `latency` / token counts | fact measures |
| exploded `traversal_info` numerics | fact measures (sparse) |
| `mechanism` / `writer_model` / `embed_model` | dim attributes / degenerate |
| `sparql` text, `sources`, `endpoint` | kept in raw provenance, **dropped from the star** |

## Quickstart (local, offline, no AWS)

```bash
make setup       # venv + deps + .env + dbt profile
make pipeline    # up (postgres+minio) → seed → ingest → dbt build → export
make dashboard   # Streamlit at http://localhost:8501
```

`make pipeline` is the offline reproducibility check: `docker compose` + the committed
`ingestion_sample/` fixtures run the whole chain with no AWS account.

Useful individual targets: `make up`, `make seed`, `make ingest`, `make dbt`,
`make export`, `make test`, `make lint`, `make parse`, `make airflow`. Run `make help`.

## Local vs cloud

Same dbt models everywhere; only the **target** and **env vars** differ (never the model
code). Local uses Postgres + MinIO in `docker compose`; cloud uses RDS `t4g.micro` + real
S3, selected by `DBT_TARGET=cloud`. See `infra/` for the cheapest-viable AWS skeleton and
the cost discipline behind each component (notably: **no MWAA**, **no Aurora**).

## CI

`.github/workflows/ci.yml` runs the **same models** end-to-end on the fixtures against
ephemeral Postgres + MinIO service containers: lint → unit tests → seed → ingest →
`dbt build` (run + tests + contracts) → idempotency test → export. Fully offline, no AWS.

## Verification status

Validated in this repo: the ingestion logic against all **81 runs / 3,461 records** of
the real fixtures (including the empty-`traversal_info` and error-row edge cases), the
unit suite, `ruff`, and `dbt parse` (Jinja/refs/contracts). The full `dbt build` + export
run against Postgres + MinIO is exercised by `make pipeline` and CI (both require Docker).

## Repo layout

```
ingestion/   EL: object storage -> raw Postgres (storage interface: local | s3)
dbt/         staging (flatten + EXPLODE) → intermediate (join + cost) → marts (star)
serve/       marts -> Parquet export
dashboard/   Streamlit (reads marts Parquet only)
airflow/     optional orchestration DAG
infra/       cheapest-viable AWS (terraform)
tests/       pytest for ingestion
```
