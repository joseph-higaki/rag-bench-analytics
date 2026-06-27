-- The cents/token -> usd_per_mtok conversion (x1e4) must reproduce the curated seed for any model
-- present in BOTH sources. Returns offending rows (any row => test fails). This is the swap point's
-- tripwire: if Portkey's unit changes or a price field is renamed, cost would silently shift unless
-- this catches the divergence. Epsilon guards float representation, not real price differences.
with portkey as (
    select * from {{ ref('stg_model_pricing_portkey') }}
),
seed as (
    select * from {{ ref('seed_model_pricing') }}
)
select
    p.model_resolved,
    p.input_usd_per_mtok  as portkey_input,  s.input_usd_per_mtok  as seed_input,
    p.output_usd_per_mtok as portkey_output, s.output_usd_per_mtok as seed_output
from portkey p
join seed s using (model_resolved)
where abs(p.input_usd_per_mtok  - s.input_usd_per_mtok)  > 0.0001
   or abs(p.output_usd_per_mtok - s.output_usd_per_mtok) > 0.0001
   or abs(coalesce(p.cache_read_usd_per_mtok, 0)  - coalesce(s.cache_read_usd_per_mtok, 0))  > 0.0001
   or abs(coalesce(p.cache_write_usd_per_mtok, 0) - coalesce(s.cache_write_usd_per_mtok, 0)) > 0.0001
