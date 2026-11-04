-- Retrieval chunks with the fiscal period of the filing they came from.
-- This is the table loaded into Postgres + pgvector for search.
select
    k.*,
    f.fiscal_year,
    f.fiscal_period,
    f.is_amendment
from {{ ref('stg_chunks') }} as k
inner join {{ ref('dim_filings') }} as f on f.accession_number = k.accession_number
