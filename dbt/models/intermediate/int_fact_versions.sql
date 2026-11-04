-- Every time a periodic filing reported a fact, with the fact's history.
--
-- A fact is (cik, taxonomy, tag, unit, period_start, period_end). The same
-- fact is re-reported as a comparative by later filings, and occasionally
-- with a different value (a restatement or reclassification).
--
--   is_primary_report: reported by the filing whose own period it is (a
--     quarter in its 10-Q, a year in its 10-K; not an amendment). This is
--     "as originally reported". The first XBRL appearance is not: periods
--     from before a company adopted XBRL were first tagged as comparatives
--     in later, sometimes restated, filings.
--   is_current: the latest report, "as currently reported".
with facts as (
    select f.*, s.period_of_report
    from {{ ref('stg_companyfacts') }} as f
    left join {{ ref('stg_filings') }} as s using (accession_number)
    where f.form_type in ({{ "'" ~ var('periodic_forms') | join("', '") ~ "'" }})
),

versioned as (
    select
        *,
        row_number() over fact_history as version,
        count(*) over fact_all as times_reported,
        count(distinct value) over fact_partition as distinct_values,
        first_value(value) over fact_history as original_value,
        first_value(filed_date) over fact_history as original_filed_date,
        last_value(value) over fact_all as current_value,
        last_value(filed_date) over fact_all as current_filed_date
    from facts
    window
        fact_partition as (partition by cik, taxonomy, tag, unit, period_start, period_end),
        fact_history as (
            partition by cik, taxonomy, tag, unit, period_start, period_end
            order by filed_date, accession_number
        ),
        fact_all as (
            partition by cik, taxonomy, tag, unit, period_start, period_end
            order by filed_date, accession_number
            rows between unbounded preceding and unbounded following
        )
)

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
    -- 52/53-week years make quarters 91 or 98 days and years 364 or 371.
    case
        when period_type = 'instant' then 'instant'
        when duration_days between 84 and 99 then 'quarter'
        when duration_days between 175 and 192 then 'six_months'
        when duration_days between 266 and 288 then 'nine_months'
        when duration_days between 357 and 372 then 'annual'
        else 'other'
    end as period_kind,
    accession_number,
    form_type,
    filed_date,
    version,
    times_reported,
    version = 1 as is_original,
    form_type in ('10-K', '10-Q') and period_end = period_of_report as is_primary_report,
    version = times_reported as is_current,
    distinct_values > 1 as is_restated,
    original_value,
    original_filed_date,
    current_value,
    current_filed_date
from versioned
