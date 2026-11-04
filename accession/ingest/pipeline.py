"""Filing HTML -> sections + chunks Parquet, with lineage on every row.

    uv run python -m accession.ingest.pipeline --tickers TSLA

Only filings whose primary document is already in the store are parsed
(download them with `accession.edgar.fetch --documents N`).
"""

from __future__ import annotations

import argparse
import json

import pyarrow as pa

from accession.config import get_settings
from accession.edgar.client import EdgarClient
from accession.edgar.submissions import resolve_tickers
from accession.edgar.xbrl import to_parquet_bytes
from accession.ingest.chunking import FilingContext, chunk_sections, embed_text
from accession.ingest.html_blocks import parse_html
from accession.ingest.sections import split_sections
from accession.storage import LocalStore, ObjectStore

_FACT_REF = pa.struct([("concept", pa.string()), ("context_id", pa.string())])

# Lineage columns are repeated on every row so a chunk file stands alone
# (and loads into Postgres) without joining back to the filing.
_LINEAGE = [
    ("cik", pa.string()),
    ("ticker", pa.string()),
    ("accession_number", pa.string()),
    ("form_type", pa.string()),
    ("filed_date", pa.string()),
    ("period_of_report", pa.string()),
    ("fiscal_year_focus", pa.string()),
    ("fiscal_period_focus", pa.string()),
]

SECTIONS_SCHEMA = pa.schema(
    [
        *_LINEAGE,
        ("section_key", pa.string()),
        ("part", pa.int8()),
        ("item", pa.string()),
        ("section_kind", pa.string()),
        ("section_title", pa.string()),
        ("section_order", pa.int16()),
        ("text", pa.string()),
        ("char_count", pa.int32()),
    ]
)

CHUNKS_SCHEMA = pa.schema(
    [
        ("chunk_id", pa.string()),
        *_LINEAGE,
        ("section_key", pa.string()),
        ("section_kind", pa.string()),
        ("section_title", pa.string()),
        ("subheading", pa.string()),
        ("ordinal", pa.int32()),
        ("chunk_type", pa.string()),
        ("table_caption", pa.string()),
        ("table_header", pa.string()),
        ("units", pa.string()),
        ("fact_refs", pa.list_(_FACT_REF)),
        ("text", pa.string()),
        ("embed_text", pa.string()),
    ]
)


def sections_key(cik: str, accession_number: str) -> str:
    return f"processed/sections/{cik}/{accession_number}.parquet"


def chunks_key(cik: str, accession_number: str) -> str:
    return f"processed/chunks/{cik}/{accession_number}.parquet"


def process_filing(html: bytes, filing: FilingContext) -> tuple[pa.Table, pa.Table]:
    info, blocks = parse_html(html)
    filing = FilingContext(**{**filing.__dict__, "info": info})
    sections = split_sections(blocks, filing.form_type)
    chunks = chunk_sections(sections, filing)

    lineage = {
        "cik": filing.cik,
        "ticker": filing.ticker,
        "accession_number": filing.accession_number,
        "form_type": filing.form_type,
        "filed_date": filing.filed_date,
        "period_of_report": filing.period_of_report,
        "fiscal_year_focus": info.fiscal_year_focus,
        "fiscal_period_focus": info.fiscal_period_focus,
    }
    section_rows = [
        {
            **lineage,
            "section_key": s.key,
            "part": s.part,
            "item": s.item,
            "section_kind": s.kind,
            "section_title": s.title,
            "section_order": s.order,
            "text": s.text,
            "char_count": len(s.text),
        }
        for s in sections
    ]
    chunk_rows = [
        {
            "chunk_id": c.chunk_id,
            **lineage,
            "section_key": c.section.key,
            "section_kind": c.section.kind,
            "section_title": c.section.title,
            "subheading": c.subheading,
            "ordinal": c.ordinal,
            "chunk_type": c.chunk_type,
            "table_caption": c.table_caption,
            "table_header": c.table_header,
            "units": c.units,
            "fact_refs": [{"concept": r.concept, "context_id": r.context_id} for r in c.fact_refs],
            "text": c.text,
            "embed_text": embed_text(c, filing),
        }
        for c in chunks
    ]
    return (
        pa.Table.from_pylist(section_rows, schema=SECTIONS_SCHEMA),
        pa.Table.from_pylist(chunk_rows, schema=CHUNKS_SCHEMA),
    )


def process_company(store: ObjectStore, cik: str, ticker: str) -> list[tuple[str, int, int]]:
    records = store.get(f"processed/edgar/filings/CIK{cik}.jsonl").decode().splitlines()
    results: list[tuple[str, int, int]] = []
    for record in map(json.loads, records):
        accession = record["accession_number"]
        html_key = f"raw/edgar/filings/{cik}/{accession}/{record['primary_document']}"
        if not store.exists(html_key):
            continue
        context = FilingContext(
            cik=cik,
            ticker=ticker,
            accession_number=accession,
            form_type=record["form_type"],
            filed_date=record["filed_date"],
            period_of_report=record["period_of_report"],
            info=None,  # type: ignore[arg-type]  # filled in from the document
        )
        sections, chunks = process_filing(store.get(html_key), context)
        store.put(sections_key(cik, accession), to_parquet_bytes(sections))
        store.put(chunks_key(cik, accession), to_parquet_bytes(chunks))
        results.append((accession, sections.num_rows, chunks.num_rows))
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+", required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    store = LocalStore(settings.data_dir)
    with EdgarClient(settings.sec_user_agent, store) as client:
        companies = resolve_tickers(client, args.tickers)  # cached ticker map, no fetch
    for company in companies:
        for accession, n_sections, n_chunks in process_company(store, company.cik, company.ticker):
            print(f"{company.ticker} {accession}: {n_sections} sections, {n_chunks} chunks")


if __name__ == "__main__":
    main()
