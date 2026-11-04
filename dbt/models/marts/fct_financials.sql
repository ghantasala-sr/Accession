-- Core metrics per company per fiscal period, straight from XBRL. Replaces
-- SecSignal's EXTRACTED_FINANCIALS, which an LLM extracted from MD&A text.
--
-- Two rows per metric and period, one per basis (see int_quarterly_values):
--   basis = 'original': as originally reported; internally consistent
--   basis = 'current':  as currently reported; reflects restatements
--
-- fiscal_period is Q1-Q4 for quarters and FY for flows over the full year.
-- Stocks (balance sheet) are point-in-time, so they only have Q1-Q4; the Q4
-- value is the year-end balance.
--
-- When a metric maps to several tags (revenue: Revenues vs
-- RevenueFromContractWithCustomer...), the highest-priority tag the company
-- reported for that period wins.
with concepts as (
    select * from {{ ref('metric_concepts') }}
),

flow_quarters as (
    select
        q.basis, q.cik, k.metric, k.priority, q.tag, q.unit,
        q.fiscal_year, q.fiscal_period, q.period_start, q.period_end,
        q.value, q.derivation, q.source_accession
    from {{ ref('int_quarterly_values') }} as q
    inner join concepts as k
        on k.kind = 'flow' and k.tag = q.tag and k.unit = q.unit and q.taxonomy = 'us-gaap'
    -- Subtracting year-to-date values is meaningless for EPS or share counts.
    where k.additive or q.derivation = 'reported'
),

versions as (
    select 'original' as basis, * from {{ ref('int_fact_versions') }} where is_primary_report
    union all
    select 'current' as basis, * from {{ ref('int_fact_versions') }} where is_current
),

flow_years as (
    select
        v.basis, v.cik, k.metric, k.priority, v.tag, v.unit,
        p.fiscal_year, 'FY' as fiscal_period, v.period_start, v.period_end,
        v.value, 'reported' as derivation, v.accession_number as source_accession
    from versions as v
    inner join concepts as k
        on k.kind = 'flow' and k.tag = v.tag and k.unit = v.unit and v.taxonomy = 'us-gaap'
    inner join {{ ref('int_fiscal_periods') }} as p
        on p.cik = v.cik
        and p.fiscal_quarter = 4
        and p.period_end = v.period_end
        and p.fiscal_year_start = v.period_start
),

stocks as (
    select
        v.basis, v.cik, k.metric, k.priority, v.tag, v.unit,
        p.fiscal_year, p.fiscal_period, p.period_start, v.period_end,
        v.value, 'reported' as derivation, v.accession_number as source_accession
    from versions as v
    inner join concepts as k
        on k.kind = 'stock' and k.tag = v.tag and k.unit = v.unit and v.taxonomy = 'us-gaap'
    inner join {{ ref('int_fiscal_periods') }} as p
        on p.cik = v.cik and p.period_end = v.period_end
    where v.period_type = 'instant'
),

candidates as (
    select * from flow_quarters
    union all
    select * from flow_years
    union all
    select * from stocks
),

ranked as (
    select
        *,
        row_number() over (
            partition by basis, cik, metric, fiscal_year, fiscal_period order by priority
        ) as rank
    from candidates
)

select
    r.basis,
    r.cik,
    c.ticker,
    r.metric,
    r.fiscal_year,
    r.fiscal_period,
    r.period_start,
    r.period_end,
    r.value,
    r.unit,
    r.tag,
    r.derivation,
    r.source_accession
from ranked as r
inner join {{ ref('dim_companies') }} as c on c.cik = r.cik
where r.rank = 1
