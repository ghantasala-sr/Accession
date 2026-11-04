{# Fiscal years where the four quarters of an additive flow don't sum to the
   reported year (beyond rounding to the reported millions). #}
{% macro quarters_vs_year(basis, min_fiscal_year=none) %}
(
    with quarters as (
        select cik, ticker, metric, fiscal_year, sum(value) as quarter_sum, count(*) as n
        from {{ ref('fct_financials') }}
        where basis = '{{ basis }}'
          and fiscal_period in ('Q1', 'Q2', 'Q3', 'Q4')
          and metric in ('revenue', 'net_income', 'operating_income', 'operating_cash_flow',
                         'capital_expenditures')
          {% if min_fiscal_year %}and fiscal_year >= {{ min_fiscal_year }}{% endif %}
        group by all
    ),

    years as (
        select cik, metric, fiscal_year, value as year_value
        from {{ ref('fct_financials') }}
        where basis = '{{ basis }}' and fiscal_period = 'FY'
    )

    select
        '{{ basis }}' as basis, q.ticker, q.metric, q.fiscal_year,
        q.quarter_sum, y.year_value, q.quarter_sum - y.year_value as difference
    from quarters as q
    inner join years as y using (cik, metric, fiscal_year)
    where q.n = 4
      and abs(q.quarter_sum - y.year_value) > 1e6
)
{% endmacro %}
