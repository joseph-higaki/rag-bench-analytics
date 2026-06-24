select *
from {{ ref('stg_runs') }}
limit 100
