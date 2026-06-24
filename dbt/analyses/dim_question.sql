select *
from {{ ref('dim_question') }}
limit 100
