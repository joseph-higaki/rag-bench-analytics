-- Conformed model token-pricing contract — the single pricing swap point. Downstream
-- cost math references ONLY this relation, so changing where prices come from touches
-- nothing else. Today a passthrough over the curated seed; later a source dispatcher
-- (seed | portkey) selected by var('pricing_source'). Rates are normalized to USD per
-- 1M tokens: any source-specific unit conversion (e.g. Portkey's cents/token) happens
-- HERE, never downstream — rate_unit pins the invariant and is the test target.
-- Unmatched models yield NULL cost downstream (left join), never a fabricated 0.
select
    provider,
    model_resolved,
    input_usd_per_mtok,
    output_usd_per_mtok,
    cache_read_usd_per_mtok,
    cache_write_usd_per_mtok,
    effective_date,
    source_note,
    'usd_per_mtok'  as rate_unit,       -- canonical unit; normalize on load, not downstream
    'seed'          as pricing_source    -- provenance; becomes per-source when the dispatcher lands
from {{ ref('seed_model_pricing') }}
