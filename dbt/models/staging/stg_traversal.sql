-- THE SCHEMA MORPH. traversal_info is schema-on-read: its keys vary by retrieval
-- mechanism, and on ~25% of fixture records it is empty ({}) or absent entirely
-- (older runs, error rows). One row per (run, question); columns are sparse by design
-- (null where a mechanism doesn't produce that measure — expected, per CLAUDE.md).
--
-- Mechanisms observed in the real data and how their keys map:
--   dense        (vector)            : top_k, num_chunks, embed_model
--   neighborhood (graph_neighborhood): hops, num_triples, num_linked
--   sparqlgen    (graph_sparqlgen)   : writer_model, writer_temperature, writer_*_tokens, sparql_valid, num_rows
--   none         (closed_book)       : traversal_info is {"retriever":"none"} or {}
-- context_tokenizer is shared by all retrieval mechanisms (the tokenizer behind the
-- context_tokens_proxy count); it and writer_temperature are sparse — newer runs only.
--
-- mechanism is derived defensively: prefer the explicit traversal_info.mechanism,
-- else infer from the top-level retriever (the reliable condition key). This keeps
-- the empty-{} and missing-key rows from collapsing to NULL.
with src as (
    select
        run_id,
        question_id,
        payload ->> 'retriever'                                  as retriever,
        coalesce(payload -> 'traversal_info', '{}'::jsonb)       as ti
    from {{ source('raw', 'scored_answer') }}
)
select
    run_id,
    question_id,

    coalesce(
        ti ->> 'mechanism',
        case
            when retriever = 'vector'                 then 'dense'
            when retriever like 'graph_neighborhood%' then 'neighborhood'
            when retriever = 'graph_sparqlgen'        then 'sparqlgen'
            when retriever = 'closed_book'            then 'none'
        end
    )                                                            as mechanism,

    -- neighborhood (graph) measures (mechanism-prefixed; neighborhood_hops is a knob -> dim)
    (ti ->> 'hops')::int                                        as neighborhood_hops,
    (ti ->> 'num_triples')::int                                 as neighborhood_num_triples,
    (ti ->> 'num_linked')::int                                  as neighborhood_num_linked,

    -- dense (vector) measures / attributes (top_k is a knob -> dim)
    (ti ->> 'top_k')::int                                       as top_k,
    (ti ->> 'num_chunks')::int                                  as dense_num_chunks,
    ti ->> 'embed_model'                                        as embed_model,

    -- sparqlgen (writer LLM) measures / attributes
    ti ->> 'writer_model'                                       as writer_model,
    {{ model_family("ti ->> 'writer_model'") }}                 as writer_model_family,
    (ti ->> 'writer_temperature')::numeric                      as writer_temperature,
    (ti ->> 'writer_input_tokens')::bigint                      as writer_input_tokens,
    (ti ->> 'writer_output_tokens')::bigint                     as writer_output_tokens,
    (ti ->> 'sparql_valid')::boolean                            as is_sparql_valid,
    (ti ->> 'num_rows')::int                                    as sparql_num_rows,

    -- shared provenance (kept here, dropped from the star)
    ti ->> 'endpoint'                                           as endpoint,
    ti ->> 'context_tokenizer'                                  as context_tokenizer
from src
