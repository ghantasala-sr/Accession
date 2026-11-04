-- Standalone fiscal-quarter values for duration facts, on two bases:
--
--   original: each value as reported in the filing for its own period (a
--             quarter in its 10-Q, a year in its 10-K). Internally consistent.
--   current:  each value as most recently reported. Picks up restatements,
--             but can mix bases (Microsoft restated FY2016 for ASC 606 in
--             its FY2018 10-K; the FY2016 quarters were never restated) and
--             inherits tagging errors in later comparatives (Amazon's FY2012
--             10-K tagged 2012 quarterly revenue with 2011 contexts).
--
-- Filings don't always report a standalone quarter:
--   * no filing reports Q4 on its own: Q4 = annual - nine months
--   * cash flow statements in 10-Qs are year-to-date only: Q2 = 6M - 3M, Q3 = 9M - 6M
-- `derivation` records which case produced each value. Subtraction is only
-- valid for additive measures; ratios such as EPS must use 'reported' rows
-- (fct_financials enforces this).
{% set bases = {'original': 'is_primary_report', 'current': 'is_current'} %}

with calendar as (
    select * from {{ ref('int_fiscal_periods') }}
),

{% for basis, flag in bases.items() %}
{{ basis }}_durations as (
    select *
    from {{ ref('int_fact_versions') }}
    where {{ flag }} and period_type = 'duration'
),

{{ basis }}_direct as (
    select
        f.cik, f.taxonomy, f.tag, f.unit, c.fiscal_year, c.fiscal_quarter,
        f.value, f.accession_number
    from {{ basis }}_durations as f
    inner join calendar as c
        on f.cik = c.cik
        and f.period_start = c.period_start
        and f.period_end = c.period_end
),

-- Year-to-date through quarter n (n = 1 is the quarter itself, n = 4 the year).
{{ basis }}_cumulative as (
    select
        f.cik, f.taxonomy, f.tag, f.unit, c.fiscal_year, c.fiscal_quarter,
        f.value, f.accession_number
    from {{ basis }}_durations as f
    inner join calendar as c
        on f.cik = c.cik
        and f.period_start = c.fiscal_year_start
        and f.period_end = c.period_end
),

{{ basis }}_joined as (
    select
        '{{ basis }}' as basis,
        g.*,
        d.value as direct_value,
        d.accession_number as direct_accession,
        ytd.value as ytd_value,
        ytd.accession_number as ytd_accession,
        prior_ytd.value as prior_ytd_value,
        prior_ytd.accession_number as prior_ytd_accession
    from (
        select cik, taxonomy, tag, unit, fiscal_year, fiscal_quarter from {{ basis }}_direct
        union
        select cik, taxonomy, tag, unit, fiscal_year, fiscal_quarter from {{ basis }}_cumulative
    ) as g
    left join {{ basis }}_direct as d
        using (cik, taxonomy, tag, unit, fiscal_year, fiscal_quarter)
    left join {{ basis }}_cumulative as ytd
        using (cik, taxonomy, tag, unit, fiscal_year, fiscal_quarter)
    left join {{ basis }}_cumulative as prior_ytd
        on prior_ytd.cik = g.cik
        and prior_ytd.taxonomy = g.taxonomy
        and prior_ytd.tag = g.tag
        and prior_ytd.unit = g.unit
        and prior_ytd.fiscal_year = g.fiscal_year
        and prior_ytd.fiscal_quarter = g.fiscal_quarter - 1
),
{% endfor %}

joined as (
    {% for basis in bases %}
    select * from {{ basis }}_joined
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    j.basis,
    j.cik,
    j.taxonomy,
    j.tag,
    j.unit,
    j.fiscal_year,
    j.fiscal_quarter,
    'Q' || j.fiscal_quarter as fiscal_period,
    c.period_start,
    c.period_end,
    case
        when j.q4_from_annual then j.ytd_value - j.prior_ytd_value
        when j.direct_value is not null then j.direct_value
        when j.fiscal_quarter = 1 then j.ytd_value
        else j.ytd_value - j.prior_ytd_value
    end as value,
    case
        when j.q4_from_annual then 'annual_minus_nine_months'
        when j.direct_value is not null then 'reported'
        when j.fiscal_quarter = 1 and j.ytd_value is not null then 'reported'
        when j.ytd_value is not null and j.prior_ytd_value is not null then 'ytd_difference'
    end as derivation,
    case
        when j.q4_from_annual then j.ytd_accession
        else coalesce(j.direct_accession, j.ytd_accession)
    end as source_accession,
    case when j.q4_from_annual or j.direct_value is null then j.prior_ytd_accession end
        as offset_accession
from (
    select
        *,
        -- Q4 comes from the audited annual statement minus nine months even
        -- when a 10-K's quarterly table tags a Q4 value: that supplementary
        -- table is where tagging errors show up (Amazon's FY2012 10-K tagged
        -- Q4 2011 revenue as Q4 2012).
        fiscal_quarter = 4 and ytd_value is not null and prior_ytd_value is not null
            as q4_from_annual
    from joined
) as j
inner join calendar as c
    on c.cik = j.cik
    and c.fiscal_year = j.fiscal_year
    and c.fiscal_quarter = j.fiscal_quarter
where j.direct_value is not null
   or j.fiscal_quarter = 1
   or (j.ytd_value is not null and j.prior_ytd_value is not null)
