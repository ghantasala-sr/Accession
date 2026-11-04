-- Periodic filings with their fiscal period and how much of each was ingested.
with chunk_counts as (
    select
        accession_number,
        count(*) as chunk_count,
        count(*) filter (where chunk_type = 'table') as table_chunk_count
    from {{ ref('stg_chunks') }}
    group by accession_number
)

select
    f.accession_number,
    f.cik,
    c.ticker,
    f.form_type,
    f.is_amendment,
    f.filed_date,
    f.period_of_report,
    p.fiscal_year,
    p.fiscal_period,
    f.primary_document,
    f.document_url,
    f.is_inline_xbrl,
    coalesce(k.chunk_count, 0) as chunk_count,
    coalesce(k.table_chunk_count, 0) as table_chunk_count,
    k.accession_number is not null as is_ingested
from {{ ref('stg_filings') }} as f
inner join {{ ref('dim_companies') }} as c on c.cik = f.cik
left join {{ ref('int_fiscal_periods') }} as p
    on p.cik = f.cik and p.period_end = f.period_of_report
left join chunk_counts as k on k.accession_number = f.accession_number
