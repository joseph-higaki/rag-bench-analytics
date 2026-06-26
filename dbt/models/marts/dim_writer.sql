-- Writer dimension — the SPARQL-generating LLM (a second actor, distinct from the
-- answering generator and from the retriever knobs). Grain: distinct
-- (writer_model, writer_temperature) observed. Only graph_sparqlgen rows have a writer;
-- every other mechanism contributes the all-null combo, so the dim carries the
-- null-writer member that the fact's writer_sk (md5 of coalesced nulls) joins to.
with observed as (
    select
        writer_model,
        writer_model_family,
        writer_temperature
    from {{ ref('int_scored_answers_enriched') }}
    group by writer_model, writer_model_family, writer_temperature
)
select
    {{ surrogate_key(['writer_model', 'writer_temperature']) }} as writer_sk,
    writer_model,
    writer_model_family,
    writer_temperature
from observed
