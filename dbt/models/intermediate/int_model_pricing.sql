-- Conformed model token-pricing contract AND the single pricing swap point: downstream cost math
-- references ONLY this relation. The source is selected by var('pricing_source') (default 'seed'):
--   'seed'    -> the curated in-repo seed (the offline default; CLAUDE.md golden rule: local runs
--                fully offline, and Portkey is a network-snapshot dependency).
--   'portkey' -> the flattened Portkey snapshot UNION a curated override for models Portkey can't
--                price (local Ollama = $0, a truth a hosted-API catalog structurally lacks).
-- Every row carries pricing_source for provenance. Rates are normalized to USD per 1M tokens
-- (rate_unit pins it); source-specific unit conversion happens upstream (stg_model_pricing_portkey),
-- never here. Unmatched models yield NULL cost downstream (left join), never a fabricated 0.
{% set pricing_source = var('pricing_source', 'seed') %}

{% if pricing_source == 'portkey' %}

with override as (
    select
        provider,
        model_resolved,
        input_usd_per_mtok,
        output_usd_per_mtok,
        cache_read_usd_per_mtok,
        cache_write_usd_per_mtok,
        effective_date,
        source_note,
        'usd_per_mtok'                          as rate_unit,
        'override'                              as pricing_source
    from {{ ref('seed_pricing_local_overrides') }}
),
portkey as (
    select
        provider,
        model_resolved,
        input_usd_per_mtok,
        output_usd_per_mtok,
        cache_read_usd_per_mtok,
        cache_write_usd_per_mtok,
        effective_date,
        'Portkey ' || provider || ' snapshot'   as source_note,
        'usd_per_mtok'                          as rate_unit,
        'portkey'                               as pricing_source
    from {{ ref('stg_model_pricing_portkey') }}
)
-- Override wins for the models it covers (the curated $0 Portkey can't supply); Portkey supplies
-- the rest. A model in both => override only, so model_resolved stays unique.
select * from override
union all
select * from portkey
where model_resolved not in (select model_resolved from override)

{% else %}

select
    provider,
    model_resolved,
    input_usd_per_mtok,
    output_usd_per_mtok,
    cache_read_usd_per_mtok,
    cache_write_usd_per_mtok,
    effective_date,
    source_note,
    'usd_per_mtok'                              as rate_unit,
    'seed'                                      as pricing_source
from {{ ref('seed_model_pricing') }}

{% endif %}
