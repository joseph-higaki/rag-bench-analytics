-- dim_token_pricing: the model token-pricing catalog as a conformed dimension. One row per priced
-- (provider, model_resolved) from int_model_pricing — i.e. the swap point, so the dim's contents
-- follow var('pricing_source') (seed | portkey∪override). Rates are USD per 1M tokens (rate_unit);
-- pricing_source carries per-row provenance. The fact references this via generator_pricing_sk /
-- writer_pricing_sk (null when a model is unpriced). Reference/provenance data for the dashboard —
-- NOT contracted (dims use tests, per the marts note); cost stays a precomputed measure on the fact.
select
    pricing_sk,
    provider,
    model_resolved,
    input_usd_per_mtok,
    output_usd_per_mtok,
    cache_read_usd_per_mtok,
    cache_write_usd_per_mtok,
    rate_unit,
    effective_date,
    source_note,
    pricing_source
from {{ ref('int_model_pricing') }}
