-- This int model is ephemeral (no physical table): resolves only via ref() + compile,
-- so it must be run through Power User / dbt compile, not as plain SQL against the DB.
select *
from {{ ref('int_scored_answers_enriched') }}
limit 100
