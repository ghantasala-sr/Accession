-- Every reported version of every XBRL fact, labelled with the fiscal period
-- it measures (not the filing's). Restatement-aware queries:
--   as originally reported: where is_original
--   as currently reported:  where is_current
select
    v.cik,
    c.ticker,
    v.taxonomy,
    v.tag,
    v.label,
    v.unit,
    v.value,
    v.period_type,
    v.period_kind,
    v.period_start,
    v.period_end,
    p.fiscal_year,
    case
        when v.period_kind in ('instant', 'quarter') then p.fiscal_period
        when v.period_kind = 'annual' then 'FY'
        when v.period_kind = 'six_months' then 'H1'
        when v.period_kind = 'nine_months' then '9M'
    end as fiscal_period,
    v.accession_number,
    v.form_type,
    v.filed_date,
    v.version,
    v.times_reported,
    v.is_original,
    v.is_current,
    v.is_restated,
    v.original_value,
    v.original_filed_date,
    v.current_value,
    v.current_filed_date
from {{ ref('int_fact_versions') }} as v
inner join {{ ref('dim_companies') }} as c on c.cik = v.cik
left join {{ ref('int_fiscal_periods') }} as p
    on p.cik = v.cik and p.period_end = v.period_end
