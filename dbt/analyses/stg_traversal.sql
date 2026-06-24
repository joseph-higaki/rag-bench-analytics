select *
from {{ ref('stg_traversal') }}
limit 100
