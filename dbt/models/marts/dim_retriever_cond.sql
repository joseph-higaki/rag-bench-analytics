-- Retriever-condition dimension — THE compared variable in the benchmark. Grain:
-- distinct (retriever, mechanism, neighborhood_hops). writer_model is deliberately NOT
-- in the grain: it's a per-run generator attribute reached via dim_generator/the fact,
-- and folding a volatile model string into this conformed dim would snowflake it.
-- Labels come from the seed; left join so an unseen retriever still produces a row.
with observed as (
    select
        retriever,
        mechanism,
        neighborhood_hops
    from {{ ref('int_scored_answers_enriched') }}
    group by retriever, mechanism, neighborhood_hops
)
select
    {{ surrogate_key(['o.retriever', 'o.mechanism', 'o.neighborhood_hops']) }} as retriever_cond_sk,
    o.retriever,
    o.mechanism,
    o.neighborhood_hops,
    l.retriever_family,
    l.is_graph,
    l.display_label
from observed o
left join {{ ref('seed_retriever_labels') }} l on o.retriever = l.retriever
