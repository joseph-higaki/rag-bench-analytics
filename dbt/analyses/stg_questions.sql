select *
from {{ ref('stg_questions') }}
limit 100
