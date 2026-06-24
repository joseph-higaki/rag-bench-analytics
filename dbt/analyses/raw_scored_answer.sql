select *
from {{ source('raw', 'scored_answer') }}
limit 100
