-- This int model is ephemeral (no physical table): resolves only via ref() + compile,
-- so it must be run through Power User / dbt compile, not as plain SQL against the DB.
select distinct
 generator_provider, 
 generator_model,
retriever, mechanism, 
writer_model,
embed_model,
endpoint,
scoring,
type_id,
template_id,
question_hop_count, 
neighborhood_hops,
num_seeds,
num_triples,
num_linked,
top_k,
num_chunks,
num_sources,
writer_temperature,
sparql_valid,
    sparql_num_rows


from {{ ref('int_scored_answers_enriched') }}
where 1=1
-- and retriever = 'graph_sparqlgen'
limit 1000
