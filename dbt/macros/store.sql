{#
  Readers for files in the object store. Staging models go through these so
  that switching to Athena (Glue tables over S3) only changes this file.

  Hive partition values are kept as strings: DuckDB would otherwise read
  cik=0001318605 as the integer 1318605 and drop the zero padding.
#}

{% macro store_path(key) -%}
  '{{ var("data_dir") }}/{{ key }}'
{%- endmacro %}

{% macro read_store_parquet(key) -%}
  read_parquet({{ store_path(key) }}, hive_partitioning = true, hive_types_autocast = false)
{%- endmacro %}

{% macro read_store_json(key) -%}
  read_json_auto({{ store_path(key) }}, maximum_object_size = 67108864)
{%- endmacro %}
