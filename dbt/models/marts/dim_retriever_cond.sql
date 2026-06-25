-- Retriever-condition dimension — THE compared variable in the benchmark. Grain:
-- distinct (retriever, mechanism, neighborhood_hops, top_k) — neighborhood_hops and top_k
-- are the per-condition knobs (graph fan depth / dense fan cap), so they belong in the
-- grain, not as fact measures (ADR-003). writer_model is deliberately NOT in the grain
-- (-> dim_writer); embed_model is functionally dependent on the corpus (-> dim_corpus,
-- ADR-004). Labels come from the seed; left join so an unseen retriever still produces a row.
with observed as (
    select
        retriever,
        mechanism,
        neighborhood_hops,
        top_k
    from {{ ref('int_scored_answers_enriched') }}
    group by retriever, mechanism, neighborhood_hops, top_k
)
select
    {{ surrogate_key(['o.retriever', 'o.mechanism', 'o.neighborhood_hops', 'o.top_k']) }} as retriever_cond_sk,
    o.retriever,
    o.mechanism,
    o.neighborhood_hops,
    o.top_k,
    l.retriever_family,
    l.is_graph,
    l.display_label
from observed o
left join {{ ref('seed_retriever_labels') }} l on o.retriever = l.retriever
