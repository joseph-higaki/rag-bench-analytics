-- Question dimension. Grain: one row per question_id. question_hop_count is the question's
-- reasoning depth (0/1/2/3+), not a retriever parameter (vs neighborhood_hops). answer_var
-- stays in stg_questions as provenance — dropped here (never a meaningful slicer).
-- question_text + ground_truth_answer_text are descriptive attributes (the dashboard's
-- ground-truth examples), not measures. type_family/type_display_label/type_description come
-- from the seed (left join so an unseen type_id still produces a row).
select
    {{ surrogate_key(['q.question_id']) }}  as question_sk,
    q.question_id,
    q.type_id,
    l.type_family,
    l.display_label                         as type_display_label,
    l.description                           as type_description,
    q.template_id,
    q.scoring,
    q.question_hop_count,
    q.num_seed_entities,
    q.question_text,
    q.ground_truth_answer_text
from {{ ref('stg_questions') }} q
left join {{ ref('seed_question_type_labels') }} l on q.type_id = l.type_id
