-- For additive flows, the four standalone quarters must add up to the
-- reported fiscal year, as originally reported, for fiscal 2019 onward.
--
-- Earlier years are covered by warn_quarters_sum_to_year instead: in the
-- first years of XBRL, companies revised earlier quarters within the year
-- (a Q3 10-Q's nine-month figure not equal to the Q1 + Q2 + Q3 10-Qs) by
-- 0.1-2%, which is a property of those filings, not of this pipeline.
{{ quarters_vs_year(basis='original', min_fiscal_year=2019) }}
