select *
from {{ ref('fct_scored_answer') }}
limit 100
