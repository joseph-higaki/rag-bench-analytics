select *
from {{ ref('stg_scored_answers') }}
limit 100
