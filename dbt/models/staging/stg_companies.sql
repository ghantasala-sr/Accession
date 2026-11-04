-- Company attributes from the submissions endpoint (first page only: the
-- CIK##########.json files, not the -submissions-NNN history pages).
select
    lpad(cast(cik as varchar), 10, '0') as cik,
    tickers[1] as ticker,
    name,
    nullif(sic, '') as sic_code,
    nullif("sicDescription", '') as sic_description,
    -- MMDD, e.g. 1231 (Tesla), 0926 (Apple's 52/53-week year), 0630 (Microsoft)
    "fiscalYearEnd" as fiscal_year_end
from {{ read_store_json('raw/edgar/submissions/CIK??????????.json') }}
