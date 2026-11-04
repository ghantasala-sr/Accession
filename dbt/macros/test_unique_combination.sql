{# Rows whose combination of `columns` is not unique (a small stand-in for
   dbt_utils.unique_combination_of_columns, to avoid a package dependency). #}
{% test dbt_utils_unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
