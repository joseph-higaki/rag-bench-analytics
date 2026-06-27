-- Flatten the Portkey pricing snapshot (raw.model_pricing: one row per provider, payload keyed by
-- model name) into one row per model, conformed to the pricing contract. THE UNIT CONVERSION LIVES
-- HERE: Portkey prices are cents/token, so usd_per_mtok = price * 1e4 (x1e6 tokens / 100 cents).
-- Uses the pay_as_you_go tier (standard), not batch_config. The Portkey model key is mapped to the
-- benchmark's model_resolved via seed_pricing_model_alias (identity when no alias row exists).
with snapshots as (
    select provider, payload, loaded_at
    from {{ source('raw', 'model_pricing') }}
),
exploded as (
    select
        s.provider,
        m.key                              as portkey_model_key,
        m.value                            as model_obj,
        s.loaded_at
    from snapshots s,
         lateral jsonb_each(s.payload) as m(key, value)
),
alias as (
    select * from {{ ref('seed_pricing_model_alias') }}
)
select
    e.provider,
    coalesce(a.model_resolved, e.portkey_model_key)                                            as model_resolved,
    e.portkey_model_key,
    (e.model_obj #>> '{pricing_config,pay_as_you_go,request_token,price}')::numeric * 1e4       as input_usd_per_mtok,
    (e.model_obj #>> '{pricing_config,pay_as_you_go,response_token,price}')::numeric * 1e4      as output_usd_per_mtok,
    (e.model_obj #>> '{pricing_config,pay_as_you_go,cache_read_input_token,price}')::numeric * 1e4  as cache_read_usd_per_mtok,
    (e.model_obj #>> '{pricing_config,pay_as_you_go,cache_write_input_token,price}')::numeric * 1e4 as cache_write_usd_per_mtok,
    e.loaded_at::date                                                                          as effective_date,
    e.loaded_at
from exploded e
left join alias a on e.portkey_model_key = a.portkey_model_key
