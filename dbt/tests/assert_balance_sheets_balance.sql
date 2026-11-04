-- Total assets must equal total liabilities and equity on every balance sheet date.
select a.ticker, a.fiscal_year, a.fiscal_period, a.value as assets, l.value as liabilities_and_equity
from {{ ref('fct_financials') }} as a
inner join {{ ref('fct_financials') }} as l
    on l.cik = a.cik
    and l.fiscal_year = a.fiscal_year
    and l.fiscal_period = a.fiscal_period
    and l.basis = a.basis
    and l.metric = 'liabilities_and_equity'
where a.metric = 'total_assets'
  and abs(a.value - l.value) > 1e6
