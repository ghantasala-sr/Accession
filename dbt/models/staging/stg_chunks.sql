-- Table-aware chunks produced by accession.ingest, one file per filing.
select
    chunk_id,
    cik,
    ticker,
    accession_number,
    form_type,
    cast(filed_date as date) as filed_date,
    cast(period_of_report as date) as period_of_report,
    fiscal_year_focus,
    fiscal_period_focus,
    section_key,
    section_kind,
    section_title,
    subheading,
    ordinal,
    chunk_type,
    table_caption,
    table_header,
    units,
    fact_refs,
    text,
    embed_text
from {{ read_store_parquet('processed/chunks/*/*.parquet') }}
