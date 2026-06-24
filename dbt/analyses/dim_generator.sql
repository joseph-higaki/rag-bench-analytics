select *
from {{ ref('dim_generator') }}
limit 100
