-- Conformed model token-pricing contract: downstream cost math references ONLY this relation.
-- Portkey is the pricing source (a landed snapshot flattened in stg_model_pricing_portkey), UNION a
-- curated override for models Portkey structurally can't price (local Ollama = $0). There is NO
-- source toggle: a different pricing source is added the normal way — a new raw source + staging
-- model — not a var. Rates are normalized to USD per 1M tokens (rate_unit pins it); source-specific
-- unit conversion happens upstream (stg_model_pricing_portkey), never here. Every row carries
-- pricing_source for provenance. Unmatched models yield NULL cost downstream (left join), never a
-- fabricated 0. pricing_sk (md5 of provider+model_resolved) is dim_token_pricing's PK and the fact's
-- pricing FK; the fact CARRIES it from the cost join (null when unpriced) rather than rehashing it
-- (ADR-003).
with override as (
    select
        {{ surrogate_key(['provider', 'model_resolved']) }} as pricing_sk,
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
        {{ surrogate_key(['provider', 'model_resolved']) }} as pricing_sk,
        provider,
        model_resolved,
        input_usd_per_mtok,
        output_usd_per_mtok,
        cache_read_usd_per_mtok,
        cache_write_usd_per_mtok,
        effective_date,
        source_note,
        'usd_per_mtok'                          as rate_unit,
        'portkey'                               as pricing_source
    from {{ ref('stg_model_pricing_portkey') }}
)
-- Override wins for the models it covers (the curated $0 Portkey can't supply); Portkey supplies
-- the rest. A model in both => override only, so model_resolved (and pricing_sk) stays unique.
select * from override
union all
select * from portkey
where model_resolved not in (select model_resolved from override)
