select *
from {{ ref('dim_run') }}
limit 100
