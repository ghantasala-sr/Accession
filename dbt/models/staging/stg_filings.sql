-- Every 10-K/10-Q (and amendment) in each company's EDGAR history.
select
    accession_number,
    cik,
    form_type,
    cast(filed_date as date) as filed_date,
    cast(period_of_report as date) as period_of_report,
    primary_document,
    is_amendment,
    is_xbrl,
    is_inline_xbrl,
    document_url
from {{ read_store_json('processed/edgar/filings/CIK*.jsonl') }}
