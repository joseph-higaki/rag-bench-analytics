select *
from {{ ref('dim_retriever_cond') }}
limit 100
