-- Question dimension. Grain: one row per question_id. hop_count here is the question's
-- reasoning depth (0/1/2/3+), not a retriever parameter.
select
    {{ surrogate_key(['question_id']) }}    as question_sk,
    question_id,
    type_id,
    template_id,
    scoring,
    answer_var,
    hop_count,
    num_seeds
from {{ ref('stg_questions') }}
