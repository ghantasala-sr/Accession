-- Spot checks against numbers read directly off the filings.
with expected (ticker, metric, fiscal_year, fiscal_period, value_millions) as (
    values
        -- Tesla Q3 2026 10-Q balance sheet and income statement
        ('TSLA', 'total_assets', 2026, 'Q3', 157517),
        ('TSLA', 'cash_and_equivalents', 2026, 'Q3', 17853),
        ('TSLA', 'revenue', 2026, 'Q3', 28046),
        -- Tesla FY2025: no filing reports Q4, so it is FY 94,827 - 9M 69,926
        ('TSLA', 'revenue', 2025, 'FY', 94827),
        ('TSLA', 'revenue', 2025, 'Q4', 24901)
)

select e.*, f.value / 1e6 as actual_millions
from expected as e
left join {{ ref('fct_financials') }} as f
    on f.basis = 'current'
    and f.ticker = e.ticker
    and f.metric = e.metric
    and f.fiscal_year = e.fiscal_year
    and f.fiscal_period = e.fiscal_period
where f.value is null or round(f.value / 1e6) != e.value_millions
