{{ config(severity='warn') }}
-- Every year on both bases, listed for review rather than failing the build.
-- Expected mismatches:
--   * original basis, early XBRL years: within-year revisions of quarters
--   * current basis: a fiscal year restated without its quarters (Microsoft
--     FY2016-17 under ASC 606) or a mis-tagged later comparative (Amazon's
--     FY2012 10-K tagging 2012 quarterly revenue with 2011 contexts)
{{ quarters_vs_year(basis='original') }}
union all
{{ quarters_vs_year(basis='current') }}
