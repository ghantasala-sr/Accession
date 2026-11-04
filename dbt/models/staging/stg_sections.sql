select
    cik,
    ticker,
    accession_number,
    form_type,
    cast(filed_date as date) as filed_date,
    cast(period_of_report as date) as period_of_report,
    section_key,
    part,
    item,
    section_kind,
    section_title,
    section_order,
    char_count,
    text
from {{ read_store_parquet('processed/sections/*/*.parquet') }}
