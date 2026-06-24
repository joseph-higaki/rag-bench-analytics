select *
from {{ ref('dim_judge') }}
limit 100
