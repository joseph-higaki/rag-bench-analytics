-- Scoring dimension. Grain: one row per scoring type (keyed on `scoring`, no judge model —
-- the run's judge identity lives on dim_run.judge_model). Ground truth is graph traversal,
-- never an LLM (CLAUDE.md); 'semantic' scoring uses a judge model only to compare against
-- that fixed ground truth, hence scoring_family distinguishes deterministic vs semantic.
-- Label from the seed.
with observed as (
    select distinct scoring
    from {{ ref('int_scored_answers_enriched') }}
    where scoring is not null
)
select
    {{ surrogate_key(['o.scoring']) }}      as scoring_sk,
    o.scoring,
    l.scoring_family,
    l.display_label
from observed o
left join {{ ref('seed_scoring_labels') }} l on o.scoring = l.scoring
