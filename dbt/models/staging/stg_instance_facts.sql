-- One row per fact in each filing's own XBRL instance: includes company
-- extension concepts and dimensional facts (segments, products, geography).
select
    cik,
    accession as accession_number,
    concept,
    taxonomy,
    split_part(concept, ':', 2) as local_name,
    context_id,
    period_type,
    period_start,
    period_end,
    dimensions,
    len(dimensions) = 0 as is_consolidated,
    unit,
    decimals,
    value,
    value_text,
    is_text_block,
    is_nil
from {{ read_store_parquet('facts/xbrl/instance/cik=*/accession=*/facts.parquet') }}
