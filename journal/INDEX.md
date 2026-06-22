# Session journal — index

Per-session token usage (deduped by API call) and focus, oldest first. The Total
column sums down the table for cumulative build cost. Sessions 01–03 predate the
journal and are recorded here by **usage only** — no dated entry (the first journaled
session is `2026-06-20.md`). Totals are counted from each transcript; treat the
latest session's as a close lower bound.

| Date | Session | Model | Input | Output | Cache read | Cache write | Total | Focus |
|---|---|---|---|---|---|---|---|---|
| 2026-06-17 | 01 | claude-opus-4-8 | 3,230 | 1,566 | 68,222 | 15,023 | 88,041 | Import biomedical-rag-bench eval results into ingestion_sample/ fixtures |
| 2026-06-17 | 02 | claude-opus-4-8 | 11,950 | 152,197 | 32,507,607 | 1,753,238 | 34,424,992 | Build the analytics pipeline: raw load + dbt dimensional model (star) |
| 2026-06-19 | 03 | claude-sonnet-4-6 | 1,408 | 23,462 | 2,499,243 | 57,674 | 2,581,787 | Airflow + dbt (Cosmos) render integration |
| 2026-06-20 | 04 | claude-opus-4-8 | 3,171 | 222,639 | 14,098,100 | 815,460 | 15,139,370 | Data-model diagrams (README); git init + first publish; CI disabled pending local validation |
| 2026-06-22 | 05 | claude-sonnet-4-6 | — | — | — | — | — | Local pipeline validation (`make pipeline`); fix dbt source schema + dim_corpus null handling |
