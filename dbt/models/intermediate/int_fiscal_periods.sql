-- Each company's fiscal calendar: one row per fiscal quarter.
--
-- Built from the structure of the filing history, not from XBRL labels:
--   * each 10-K's period end closes a fiscal year (that quarter is Q4)
--   * the 10-Qs between two year ends are Q1, Q2, Q3 in order
--   * the fiscal year's number is the calendar year it ends in, plus the
--     company's naming offset (0 for all six starter companies; retailers
--     whose year ends in early February often name it for the prior year)
--
-- The offset is a majority vote over the company's 10-Ks because filers do
-- mis-tag dei:DocumentFiscalYearFocus: NVIDIA labelled its FY2011 quarters
-- "2010 Q2, Q3, Q3" and the FY2011 10-K "2010". Trusting labels would give
-- one fiscal quarter two different end dates.
with period_ends as (
    select distinct cik, period_of_report as period_end, form_type = '10-K' as is_year_end
    from {{ ref('stg_filings') }}
    where form_type in ('10-K', '10-Q') and period_of_report is not null
),

naming_offset as (
    select cik, mode(f.filing_fiscal_year - year(f.period_end)) as offset_years
    from {{ ref('stg_companyfacts') }} as f
    inner join {{ ref('stg_filings') }} as s using (cik, accession_number)
    where s.form_type = '10-K' and f.period_end = s.period_of_report
    group by cik
),

with_year as (
    select
        p.*,
        -- The year end closing this period: this one, or the next 10-K's.
        min(case when is_year_end then period_end end) over (
            partition by cik order by period_end
            rows between current row and unbounded following
        ) as year_end,
        max(case when is_year_end then period_end end) over (
            partition by cik order by period_end
            rows between unbounded preceding and 1 preceding
        ) as previous_year_end,
        lag(period_end) over (partition by cik order by period_end) as previous_end
    from period_ends as p
),

numbered as (
    select
        w.*,
        -- Quarters after the latest 10-K belong to the year still in progress.
        coalesce(year(w.year_end), year(w.previous_year_end) + 1) + o.offset_years as fiscal_year,
        row_number() over (
            partition by w.cik, coalesce(w.year_end, w.previous_year_end + 1)
            order by w.period_end
        ) as position
    from with_year as w
    inner join naming_offset as o using (cik)
)

select
    cik,
    fiscal_year,
    case when is_year_end then 4 else position end as fiscal_quarter,
    'Q' || case when is_year_end then 4 else position end as fiscal_period,
    -- Null for a company's first quarter on record, or after a gap in filings.
    case when period_end - previous_end between 84 and 99 then previous_end + 1 end as period_start,
    period_end,
    case
        when period_end - previous_year_end between 84 and 380 then previous_year_end + 1
    end as fiscal_year_start
from numbered
-- A fiscal year with more than three 10-Qs (a transition period) can't be
-- numbered by position; leave those quarters out rather than mislabel them.
where is_year_end or position <= 3
