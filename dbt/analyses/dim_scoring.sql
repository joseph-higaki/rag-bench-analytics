select *
from {{ ref('dim_scoring') }}
limit 100
