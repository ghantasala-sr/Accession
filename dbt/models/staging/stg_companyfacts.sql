-- One row per fact per filing that reported it, from SEC's companyfacts API.
-- No custom (tsla:) or dimensional facts here; see stg_instance_facts.
select
    cik,
    taxonomy,
    tag,
    label,
    unit,
    value,
    period_type,
    period_start,
    period_end,
    duration_days,
    accession_number,
    form_type,
    filed_date,
    -- These label the FILING, not the fact's period. Use int_fiscal_periods.
    filing_fiscal_year,
    filing_fiscal_period,
    frame
from {{ read_store_parquet('facts/xbrl/companyfacts/cik=*/facts.parquet') }}
