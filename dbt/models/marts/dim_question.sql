-- Question dimension. Grain: one row per question_id. question_hop_count is the question's
-- reasoning depth (0/1/2/3+), not a retriever parameter (vs neighborhood_hops). answer_var
-- stays in stg_questions as provenance — dropped here (never a meaningful slicer).
-- question_text + ground_truth_answer_text are descriptive attributes (the dashboard's
-- ground-truth examples), not measures.
select
    {{ surrogate_key(['question_id']) }}    as question_sk,
    question_id,
    type_id,
    template_id,
    scoring,
    question_hop_count,
    num_seed_entities,
    question_text,
    ground_truth_answer_text
from {{ ref('stg_questions') }}
