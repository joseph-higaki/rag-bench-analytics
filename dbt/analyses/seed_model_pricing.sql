select *
from {{ ref('seed_model_pricing') }}
limit 100
