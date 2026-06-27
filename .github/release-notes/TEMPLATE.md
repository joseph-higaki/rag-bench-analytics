<!--
Copy this file to <version>.md (e.g. v0.1.0.md) and fill it in.
The release workflow reads .github/release-notes/<tag>.md as the Release body and
FAILS the release if that file is missing at the tagged commit. TEMPLATE.md itself is
ignored by the workflow (it only matches v*.*.*.md).
Group changes by the repo's layers; omit any section with nothing to report.
-->

# <version> — <descriptive title>

One-paragraph summary of what shipped in this release and why it matters.

## Ingestion

- EL changes: new sources landed, `raw.*` shape, idempotency/keying changes.

## dbt models & marts

- Staging / intermediate / marts changes. Call out **contract changes explicitly**
  (added/dropped/retyped mart columns, grain changes) — these drive the version bump.

## Dashboard

- New views, metric/label changes, anything a viewer would notice.

## Infra

- docker-compose, CI, Terraform, env/contract changes for self-hosting.

## Reproducing locally

```bash
git clone https://github.com/joseph-higaki/rag-bench-analytics
cd rag-bench-analytics
git checkout <version>
make pipeline   # docker compose up + seed fixtures + ingest + dbt build, fully offline
make dashboard  # v1 on :8501, v2 on :8502
```

## Data coverage at this release (optional)

What the marts actually contain at this tag — the analytics analog of a config block.
e.g. generators present, retriever conditions, harness/question-type coverage; null
where a mechanism or corpus didn't produce it.
