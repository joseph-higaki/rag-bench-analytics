select *
from {{ ref('dim_corpus') }}
limit 100
