-- Flatten the shared question bank (questions.jsonl). question_hop_count is the QUESTION's
-- reasoning depth, parsed from the type_id naming convention (e.g. 01_0hop_attribute,
-- 04_3plus_hop_traversal) — distinct from a graph retriever's neighborhood_hops.
-- ground_truth is polymorphic (scalar string OR array), so it's kept as JSON text
-- for provenance; the star doesn't measure on it.
with src as (
    select question_id, payload
    from {{ source('raw', 'question') }}
)
select
    question_id,
    payload ->> 'type_id'                                       as type_id,
    payload ->> 'template_id'                                   as template_id,
    payload ->> 'scoring'                                       as scoring,
    payload ->> 'answer_var'                                    as answer_var,
    payload -> 'ground_truth'                                   as ground_truth_json,
    case
        when payload ->> 'type_id' like '%3plus_hop%' then 3
        when payload ->> 'type_id' like '%2hop%'       then 2
        when payload ->> 'type_id' like '%1hop%'       then 1
        when payload ->> 'type_id' like '%0hop%'       then 0
    end                                                         as question_hop_count,
    jsonb_array_length(coalesce(payload -> 'seeds', '[]'::jsonb)) as num_seed_entities
from src
